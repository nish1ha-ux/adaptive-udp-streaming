"""
Adaptive UDP Streaming — UDP Receiver
======================================

Authors: Nishtha Bhushan, Prakhar Manish Srivastava
Module: src/receiver.py

Listens for incoming sequenced DATA packets over UDP, reassembles them
in sequence order, writes the reconstructed file to disk, and verifies
its SHA-256 checksum.

Usage (command line)::

    python -m src.receiver --host 127.0.0.1 --port 5005 --output received.bin
    python -m src.receiver --host 127.0.0.1 --port 5005 --output received.bin --arq stop-and-wait

Reliability Features (Prakhar):
* Stop-and-Wait ARQ: sends an ACK datagram for each valid DATA and END packet.
* Safe duplicate handling: resends ACKs for duplicates without corrupting payload.
* Safe out-of-order handling: never emits an invalid ACK(0) before packet 0 arrives.
* Reliable END handshake: sends ACK for END upon complete transfer acceptance.
* Linger policy: lingers briefly after completion to catch lost END ACK retransmissions.
* Fully backward compatible with unmanaged UDP blast mode (`--arq none`).
"""

import argparse
import hashlib
import logging
import socket
import sys
import time
from pathlib import Path

from src.packet import (
    MAX_DATAGRAM_SIZE,
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    PacketError,
    create_ack_packet,
    decode_end_payload,
    decode_packet,
    encode_packet,
)
from src.reliability import StopAndWaitReceiver

logger = logging.getLogger(__name__)

# Modes in which the receiver sends ACK datagrams back to the sender.
ACKING_MODES = ("stop-and-wait", "selective")


def receive_file(
    output_path,
    host="127.0.0.1",
    port=5005,
    timeout=30.0,
    post_end_timeout=5.0,
    arq="stop-and-wait",
    linger_timeout=1.0,
    send_ack=None,
):
    """Listen for incoming packets, send ACKs, and reconstruct the file.

    Parameters:
        output_path:      Path where the reconstructed file will be saved.
        host:             Local address to bind.
        port:             Local UDP port to bind.
        timeout:          Seconds to wait for packets before giving up.
        post_end_timeout: Seconds to wait after END for delayed DATA packets.
        arq:              Reliability mode: 'stop-and-wait' (default), 'selective' (ACK every valid
                          DATA packet by its own sequence number, for the paced adaptive sender)
                          or 'none'.
        linger_timeout:   Seconds to linger after completion to resend lost END ACKs.
        send_ack:         Compatibility switch used by the paced sender and its experiments:
                          True selects 'selective', False selects 'none'. Overrides *arq*.

    Returns:
        dict with keys ``total_bytes``, ``total_packets``,
        ``duplicates``, ``sha256``.

    Raises:
        ValueError:    If timeout values or arq mode are invalid.
        TimeoutError:  If no packets arrive within *timeout*.
        RuntimeError:  If the stream cannot be fully reconstructed.
    """
    if timeout <= 0:
        raise ValueError(f"Timeout must be positive, got {timeout!r}.")
    if post_end_timeout < 0:
        raise ValueError(f"Post-END timeout cannot be negative, got {post_end_timeout!r}.")
    if linger_timeout < 0:
        raise ValueError(f"Linger timeout cannot be negative, got {linger_timeout!r}.")
    if send_ack is not None:
        arq = "selective" if send_ack else "none"
    if arq not in ("stop-and-wait", "selective", "none"):
        raise ValueError(
            f"Unsupported ARQ mode: {arq!r}. Valid modes are 'stop-and-wait', 'selective' and 'none'."
        )

    received: dict[int, bytes] = {}
    duplicates: int = 0
    end_received: bool = False
    expected_packets: int | None = None
    last_sender_address = None

    receiver_arq = StopAndWaitReceiver() if arq == "stop-and-wait" else None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # On Windows, disable WSAECONNRESET on UDP socket to prevent ICMP Port Unreachable
        # from disrupting subsequent recvfrom calls.
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass
        sock.settimeout(timeout)
        sock.bind((host, port))
        logger.info("Listening on %s:%d (timeout=%.1fs, ARQ=%s)", host, port, timeout, arq)

        while True:
            # Check completion: END received and all DATA packets present.
            if (
                end_received
                and expected_packets is not None
                and len(received) >= expected_packets
            ):
                logger.info("All %d DATA packets received — stream complete.", expected_packets)
                break

            try:
                data, address = sock.recvfrom(MAX_DATAGRAM_SIZE + 64)
                last_sender_address = address
            except socket.timeout:
                if end_received and expected_packets is not None:
                    # Post-END timeout expired; finalise with what we have.
                    break
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for packets. "
                    f"Ensure the sender is running and targeting {host}:{port}."
                )
            except ConnectionResetError:
                # On Windows, Winsock raises ConnectionResetError (WSAECONNRESET 10054) on recvfrom
                # when a previously sent UDP packet (e.g. an ACK) received an ICMP Port Unreachable.
                logger.debug("Received ICMP Port Unreachable (ConnectionResetError); ignoring.")
                continue

            # Attempt to decode datagram safely.
            try:
                pkt = decode_packet(data)
            except PacketError as exc:
                logger.warning("Ignored invalid packet from %s: %s", address, exc)
                continue

            # ---- DATA ----
            if pkt.packet_type == TYPE_DATA:
                if pkt.sequence_number in received:
                    duplicates += 1
                    logger.debug("Duplicate DATA seq=%d from %s (ignored, resending ACK)", pkt.sequence_number, address)
                    if arq in ACKING_MODES:
                        ack_pkt = create_ack_packet(pkt.sequence_number, timestamp_ns=pkt.timestamp_ns)
                        sock.sendto(encode_packet(ack_pkt), address)
                    continue

                # If total expected is known, reject out-of-range packets.
                if expected_packets is not None and pkt.sequence_number >= expected_packets:
                    logger.warning(
                        "DATA seq=%d out of range (expected 0–%d) — ignored.",
                        pkt.sequence_number, expected_packets - 1,
                    )
                    continue

                # Buffer the payload.
                received[pkt.sequence_number] = pkt.payload

                # Process through receiver ARQ helper.
                if arq == "stop-and-wait" and receiver_arq is not None:
                    deliver, ack_to_send = receiver_arq.process_packet(pkt.sequence_number)
                    if ack_to_send is not None:
                        ack_pkt = create_ack_packet(ack_to_send, timestamp_ns=pkt.timestamp_ns)
                        sock.sendto(encode_packet(ack_pkt), address)
                        logger.debug("Sent ACK seq=%d to %s", ack_to_send, address)
                elif arq == "selective":
                    ack_pkt = create_ack_packet(pkt.sequence_number, timestamp_ns=pkt.timestamp_ns)
                    sock.sendto(encode_packet(ack_pkt), address)
                    logger.debug("Sent selective ACK seq=%d to %s", pkt.sequence_number, address)

                logger.info(
                    "DATA seq=%d %d bytes (buffered %d)",
                    pkt.sequence_number, len(pkt.payload), len(received),
                )

                # If END arrived earlier, check if stream is now complete and send END ACK.
                if end_received and expected_packets is not None and len(received) >= expected_packets:
                    if arq in ACKING_MODES:
                        end_ack = create_ack_packet(expected_packets, timestamp_ns=pkt.timestamp_ns)
                        sock.sendto(encode_packet(end_ack), address)
                        logger.info("Sent deferred ACK for END seq=%d to %s", expected_packets, address)
                    break

            # ---- END ----
            elif pkt.packet_type == TYPE_END:
                if end_received:
                    logger.debug("Duplicate END packet received from %s.", address)
                    if (
                        arq in ACKING_MODES
                        and expected_packets is not None
                        and len(received) >= expected_packets
                    ):
                        end_ack = create_ack_packet(pkt.sequence_number, timestamp_ns=pkt.timestamp_ns)
                        sock.sendto(encode_packet(end_ack), address)
                        logger.debug("Resent ACK for duplicate END seq=%d to %s", pkt.sequence_number, address)
                    continue

                end_received = True

                try:
                    expected_packets = decode_end_payload(pkt.payload)
                except PacketError:
                    expected_packets = pkt.sequence_number
                    logger.warning("END payload decode failed; using sequence_number=%d", expected_packets)

                logger.info("END received — expecting %d DATA packets (have %d so far).", expected_packets, len(received))

                if len(received) >= expected_packets:
                    # All DATA packets already received; acknowledge END immediately.
                    if arq in ACKING_MODES:
                        end_ack = create_ack_packet(pkt.sequence_number, timestamp_ns=pkt.timestamp_ns)
                        sock.sendto(encode_packet(end_ack), address)
                        logger.info("Sent ACK for END packet (seq=%d) to %s", pkt.sequence_number, address)
                    break
                else:
                    # Missing packets: switch to shorter post-END timeout to await retransmissions.
                    if post_end_timeout > 0:
                        sock.settimeout(post_end_timeout)

            # ---- OTHER (e.g. unexpected ACK on receiver) ----
            else:
                logger.debug("Ignored unexpected packet type %d from %s", pkt.packet_type, address)

        # --- Reconstruction ---------------------------------------------------
        if expected_packets is None:
            if received:
                expected_packets = max(received.keys()) + 1
            else:
                expected_packets = 0

        missing = [seq for seq in range(expected_packets) if seq not in received]

        if missing:
            raise RuntimeError(
                f"Cannot reconstruct file: {len(missing)} of {expected_packets} DATA packet(s) missing. "
                f"Missing sequence numbers: {missing}"
            )

        # Reassemble in sequence order.
        reconstructed = b"".join(received[seq] for seq in range(expected_packets))

        # Write to disk safely.
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(reconstructed)

        file_hash = hashlib.sha256(reconstructed).hexdigest()

        logger.info("Saved: %s (%d bytes)", destination, len(reconstructed))
        logger.info("SHA-256: %s", file_hash)
        if duplicates:
            logger.info("Duplicates ignored: %d", duplicates)

        # --- Linger Policy ----------------------------------------------------
        # Linger briefly to absorb retransmitted END packets in case final ACK was dropped.
        if (
            arq in ACKING_MODES
            and linger_timeout > 0
            and last_sender_address is not None
            and expected_packets is not None
        ):
            logger.debug("Entering linger state for %.2fs", linger_timeout)
            linger_end = time.monotonic() + linger_timeout
            while time.monotonic() < linger_end:
                rem_linger = max(0.001, linger_end - time.monotonic())
                sock.settimeout(rem_linger)
                try:
                    ldata, laddr = sock.recvfrom(MAX_DATAGRAM_SIZE + 64)
                    try:
                        lpkt = decode_packet(ldata)
                        if lpkt.packet_type == TYPE_END or (
                            lpkt.packet_type == TYPE_DATA and lpkt.sequence_number in received
                        ):
                            lack = create_ack_packet(lpkt.sequence_number, timestamp_ns=lpkt.timestamp_ns)
                            sock.sendto(encode_packet(lack), laddr)
                            logger.debug("Linger: resent ACK seq=%d to %s", lpkt.sequence_number, laddr)
                    except PacketError:
                        pass
                except (socket.timeout, OSError):
                    break

    return {
        "total_bytes": len(reconstructed),
        "total_packets": expected_packets,
        "duplicates": duplicates,
        "sha256": file_hash,
    }


def main():
    """Command-line entry point for the UDP receiver."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Receive a file using Adaptive UDP Streaming",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Local address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=5005,
        help="Local UDP port to bind (default: 5005)",
    )
    parser.add_argument(
        "--output", default="received.bin",
        help="Output file path (default: received.bin)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Seconds to wait for packets (default: 30)",
    )
    parser.add_argument(
        "--post-end-timeout", type=float, default=5.0,
        help="Extra seconds to wait after END for delayed DATA (default: 5)",
    )
    parser.add_argument(
        "--arq", choices=["stop-and-wait", "selective", "none"], default="stop-and-wait",
        help="Reliability mechanism (default: stop-and-wait)",
    )
    parser.add_argument(
        "--linger-timeout", type=float, default=1.0,
        help="Seconds to linger after completion for lost END ACKs (default: 1.0)",
    )

    args = parser.parse_args()

    try:
        receive_file(
            args.output,
            args.host,
            args.port,
            args.timeout,
            args.post_end_timeout,
            arq=args.arq,
            linger_timeout=args.linger_timeout,
        )
    except (ValueError, TimeoutError, RuntimeError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
