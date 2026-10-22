"""
Adaptive UDP Streaming — UDP Sender
====================================

Authors: Nishtha Bhushan, Prakhar Manish Srivastava
Module: src/sender.py

Reads a file, splits it into sequenced DATA packets, transmits them
over UDP using Stop-and-Wait ARQ reliability, and finishes with an END
packet that carries the total DATA-packet count.

Usage (command line)::

    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005
    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005 --arq stop-and-wait --max-retries 5

Reliability Features (Prakhar):
* Stop-and-Wait ARQ: exactly one unacknowledged packet in flight.
* Retransmission on timeout with Jacobson/Karels RTO estimation and exponential backoff.
* Karn's algorithm: retransmitted packets do not update RTT estimates.
* Reliable END handshake: END packet is acknowledged before sender terminates.
* Fully backward compatible CLI with optional baseline unmanaged mode (`--arq none`).
"""

import argparse
import hashlib
import logging
import socket
import sys
import time
from pathlib import Path

from src.packet import (
    HEADER_SIZE,
    MAX_DATAGRAM_SIZE,
    MAX_PAYLOAD_SIZE,
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    Packet,
    PacketError,
    create_packet,
    decode_packet,
    encode_end_payload,
    encode_packet,
)
from src.reliability import (
    MaxRetriesExceededError,
    RttEstimator,
    StopAndWaitARQ,
)

logger = logging.getLogger(__name__)


def _send_packet_reliably(
    sock: socket.socket,
    pkt: Packet,
    dest: tuple[str, int],
    arq: StopAndWaitARQ,
) -> int:
    """Send a packet and wait for its matching ACK using StopAndWaitARQ.

    Parameters:
        sock: Socket used for transmission and ACK reception.
        pkt:  Packet to transmit.
        dest: (host, port) tuple.
        arq:  StopAndWaitARQ controller.

    Returns:
        Number of retransmissions executed for this packet.

    Raises:
        MaxRetriesExceededError: If retries exceed max_retries.
    """
    encoded = encode_packet(pkt)
    arq.start_packet(pkt.sequence_number)
    sock.sendto(encoded, dest)

    packet_name = "DATA" if pkt.packet_type == TYPE_DATA else "END"
    logger.debug(
        "Sent %s seq=%d (%d bytes), awaiting ACK (RTO=%.3fs)",
        packet_name, pkt.sequence_number, len(pkt.payload), arq.rto,
    )

    while arq.is_in_flight:
        rem = arq.remaining_time_to_timeout()
        if rem <= 0:
            if arq.check_timeout():
                retries = arq.handle_timeout()
                logger.warning(
                    "Timeout waiting for ACK seq=%d. Retransmitting (%d/%d, RTO=%.3fs)",
                    pkt.sequence_number, retries, arq.max_retries, arq.rto,
                )
                sock.sendto(encoded, dest)
                continue
            rem = 0.001

        sock.settimeout(max(0.001, rem))
        try:
            raw_ack, _ = sock.recvfrom(MAX_DATAGRAM_SIZE + 64)
        except socket.timeout:
            if arq.check_timeout():
                retries = arq.handle_timeout()
                logger.warning(
                    "Timeout waiting for ACK seq=%d. Retransmitting (%d/%d, RTO=%.3fs)",
                    pkt.sequence_number, retries, arq.max_retries, arq.rto,
                )
                sock.sendto(encoded, dest)
            continue
        except OSError as exc:
            logger.debug("Socket error during ACK reception: %s", exc)
            continue

        try:
            ack_pkt = decode_packet(raw_ack)
        except PacketError as exc:
            logger.debug("Ignored malformed datagram on sender socket: %s", exc)
            continue

        if ack_pkt.packet_type == TYPE_ACK:
            matched = arq.on_ack_received(ack_pkt.sequence_number)
            if matched:
                logger.debug(
                    "Received matching ACK for seq=%d (RTO=%.3fs)",
                    ack_pkt.sequence_number, arq.rto,
                )
                break
            else:
                logger.debug(
                    "Ignored duplicate/unexpected ACK seq=%d (in flight: %s)",
                    ack_pkt.sequence_number, arq.current_sequence_number,
                )

    return arq.retries


def send_file(
    input_path,
    host="127.0.0.1",
    port=5005,
    packet_interval=0.001,
    arq="stop-and-wait",
    max_retries=5,
    initial_rto=1.0,
    min_rto=None,
    max_rto=60.0,
):
    """Send a file as sequenced UDP packets with optional Stop-and-Wait ARQ.

    Parameters:
        input_path:      Path to the file to send.
        host:            Destination IP address.
        port:            Destination UDP port (1–65535).
        packet_interval: Seconds to sleep between packets (≥ 0).
        arq:             Reliability mode: 'stop-and-wait' (default) or 'none'.
        max_retries:     Maximum retransmission attempts per packet (default: 5).
        initial_rto:     Initial retransmission timeout in seconds (default: 1.0).

    Returns:
        dict with keys ``total_bytes``, ``total_packets``, ``sha256``,
        ``retransmissions``, ``rto``.

    Raises:
        FileNotFoundError:       If *input_path* does not exist.
        ValueError:              If *port*, *packet_interval*, or ARQ parameters are invalid.
        MaxRetriesExceededError: If a packet exceeds max_retries without being ACKed.
    """
    path = Path(input_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(
            f"Port must be an integer between 1 and 65535, got {port!r}."
        )

    if packet_interval < 0:
        raise ValueError(
            f"Packet interval cannot be negative, got {packet_interval!r}."
        )

    if arq not in ("stop-and-wait", "none"):
        raise ValueError(f"Unsupported ARQ mode: {arq!r}. Valid modes are 'stop-and-wait' and 'none'.")

    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()

    # Compute number of DATA packets. An empty file produces 0 DATA packets.
    if len(data) == 0:
        total_packets = 0
    else:
        total_packets = (len(data) + MAX_PAYLOAD_SIZE - 1) // MAX_PAYLOAD_SIZE

    logger.info(
        "Sending %s (%d bytes, %d DATA packet(s), SHA-256: %s, ARQ: %s)",
        path.name, len(data), total_packets, file_hash, arq,
    )

    total_retransmissions = 0
    arq_controller = None

    if arq == "stop-and-wait":
        effective_min_rto = 0.2 if min_rto is None else float(min_rto)
        if initial_rto < effective_min_rto:
            effective_min_rto = float(initial_rto)
        rtt_estimator = RttEstimator(
            initial_rto=initial_rto,
            min_rto=effective_min_rto,
            max_rto=max(max_rto, initial_rto),
        )
        arq_controller = StopAndWaitARQ(rtt_estimator=rtt_estimator, max_retries=max_retries)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # Send DATA packets.
        for seq, offset in enumerate(range(0, max(len(data), 1) if len(data) else 0, MAX_PAYLOAD_SIZE)):
            payload = data[offset:offset + MAX_PAYLOAD_SIZE]
            pkt = create_packet(TYPE_DATA, seq, payload)

            if arq == "stop-and-wait" and arq_controller is not None:
                retrans = _send_packet_reliably(sock, pkt, (host, port), arq_controller)
                total_retransmissions += retrans
                logger.info("Delivered DATA seq=%d %d bytes (retries=%d)", seq, len(payload), retrans)
            else:
                sock.sendto(encode_packet(pkt), (host, port))
                logger.info("Sent DATA seq=%d %d bytes", seq, len(payload))

            if packet_interval and seq < total_packets - 1:
                time.sleep(packet_interval)

        # Send END packet. Payload carries the total DATA packet count.
        end_payload = encode_end_payload(total_packets)
        end_pkt = create_packet(TYPE_END, total_packets, end_payload)

        if arq == "stop-and-wait" and arq_controller is not None:
            retrans = _send_packet_reliably(sock, end_pkt, (host, port), arq_controller)
            total_retransmissions += retrans
            logger.info("Delivered END packet (total_data_packets=%d, retries=%d)", total_packets, retrans)
        else:
            sock.sendto(encode_packet(end_pkt), (host, port))
            logger.info("Sent END packet (total_data_packets=%d)", total_packets)

    final_rto = arq_controller.rto if arq_controller is not None else None
    logger.info(
        "Transfer complete: %d bytes, %d DATA packets, %d retransmissions, SHA-256: %s",
        len(data), total_packets, total_retransmissions, file_hash,
    )

    return {
        "total_bytes": len(data),
        "total_packets": total_packets,
        "retransmissions": total_retransmissions,
        "rto": final_rto,
        "sha256": file_hash,
    }


def main():
    """Command-line entry point for the UDP sender."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Send a file using Adaptive UDP Streaming",
    )
    parser.add_argument("file", help="Path to the file to send")
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Destination host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=5005,
        help="Destination UDP port (default: 5005)",
    )
    parser.add_argument(
        "--interval", type=float, default=0.001,
        help="Delay between DATA packets in seconds (default: 0.001)",
    )
    parser.add_argument(
        "--arq", choices=["stop-and-wait", "none"], default="stop-and-wait",
        help="Reliability mechanism (default: stop-and-wait)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=5,
        help="Maximum retransmissions per packet (default: 5)",
    )
    parser.add_argument(
        "--initial-rto", type=float, default=1.0,
        help="Initial retransmission timeout in seconds (default: 1.0)",
    )

    args = parser.parse_args()

    try:
        send_file(
            args.file,
            args.host,
            args.port,
            args.interval,
            arq=args.arq,
            max_retries=args.max_retries,
            initial_rto=args.initial_rto,
        )
    except (FileNotFoundError, ValueError, MaxRetriesExceededError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
