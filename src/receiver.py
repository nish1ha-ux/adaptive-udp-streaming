"""
Adaptive UDP Streaming — UDP Receiver
======================================

Author: Nishtha Bhushan
Module: src/receiver.py

Listens on a UDP socket, receives DATA and END packets, verifies
checksums, reassembles the file in sequence order, and writes the
result to disk.

Usage (command line)::

    python -m src.receiver --host 127.0.0.1 --port 5005 --output out.bin

Completion logic
----------------
UDP does not guarantee ordering, so the END packet may arrive before
all DATA packets.  The receiver uses the total-DATA-packet count
embedded in the END payload (see ``packet.encode_end_payload``) to
determine when every chunk has been received.

After receiving the END packet the receiver continues to listen for
late-arriving DATA packets until either all expected packets have
been received or a configurable *post-END timeout* elapses.

If the timeout expires and packets are still missing, the receiver
raises ``RuntimeError`` listing the missing sequence numbers.

Limitations
-----------
* The receiver does not send ACKs or request retransmissions.
  Those responsibilities belong to Prakhar's reliability module.
* The receiver buffers all payloads in memory before writing.
"""

import argparse
import hashlib
import logging
import socket
import sys
from pathlib import Path

from src.packet import (
    TYPE_DATA,
    TYPE_END,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    PacketError,
    decode_packet,
    decode_end_payload,
)

logger = logging.getLogger(__name__)

MAX_DATAGRAM_SIZE: int = HEADER_SIZE + MAX_PAYLOAD_SIZE
"""Buffer size for ``recvfrom``.  Large enough for the biggest valid
datagram."""


def receive_file(
    output_path,
    host="127.0.0.1",
    port=5005,
    timeout=30.0,
    post_end_timeout=5.0,
):
    """Receive UDP packets and reconstruct the original file.

    Parameters:
        output_path:      Where to write the reconstructed file.
        host:             Local address to bind.
        port:             Local UDP port to bind.
        timeout:          Seconds to wait for *any* packet before giving
                          up entirely.
        post_end_timeout: After receiving END, how many extra seconds to
                          wait for delayed DATA packets before finalising.

    Returns:
        dict with keys ``total_bytes``, ``total_packets``,
        ``duplicates``, ``sha256``.

    Raises:
        ValueError:    If timeout values are invalid.
        TimeoutError:  If no packets arrive within *timeout*.
        RuntimeError:  If the stream cannot be fully reconstructed
                       (missing packets).
    """
    if timeout <= 0:
        raise ValueError(
            f"Timeout must be positive, got {timeout!r}."
        )
    if post_end_timeout < 0:
        raise ValueError(
            f"Post-END timeout cannot be negative, got {post_end_timeout!r}."
        )

    received: dict[int, bytes] = {}
    duplicates: int = 0
    end_received: bool = False
    expected_packets: int | None = None  # Set when END arrives.

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.bind((host, port))
        logger.info("Listening on %s:%d (timeout=%.1fs)", host, port, timeout)

        while True:
            # Check completion: END received and all DATA packets present.
            if (
                end_received
                and expected_packets is not None
                and len(received) >= expected_packets
            ):
                logger.info(
                    "All %d DATA packets received — stream complete.",
                    expected_packets,
                )
                break

            try:
                data, address = sock.recvfrom(MAX_DATAGRAM_SIZE + 64)
            except socket.timeout:
                if end_received and expected_packets is not None:
                    # Post-END timeout expired; finalise with what we have.
                    break
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for packets. "
                    "Ensure the sender is running and targeting the "
                    f"correct host/port ({host}:{port})."
                )

            # Attempt to decode.
            try:
                pkt = decode_packet(data)
            except PacketError as exc:
                logger.warning(
                    "Ignored invalid packet from %s: %s", address, exc,
                )
                continue

            # ---- DATA ----
            if pkt.packet_type == TYPE_DATA:
                if pkt.sequence_number in received:
                    duplicates += 1
                    logger.debug(
                        "Duplicate DATA seq=%d from %s (ignored)",
                        pkt.sequence_number, address,
                    )
                    continue

                # If we already know the expected count, reject out-of-range.
                if (
                    expected_packets is not None
                    and pkt.sequence_number >= expected_packets
                ):
                    logger.warning(
                        "DATA seq=%d out of range (expected 0–%d) — ignored.",
                        pkt.sequence_number, expected_packets - 1,
                    )
                    continue

                received[pkt.sequence_number] = pkt.payload
                logger.info(
                    "DATA seq=%d  %d bytes  (buffered %d)",
                    pkt.sequence_number, len(pkt.payload), len(received),
                )

            # ---- END ----
            elif pkt.packet_type == TYPE_END:
                if end_received:
                    logger.debug("Duplicate END packet (ignored).")
                    continue

                end_received = True

                # Extract total DATA packet count from END payload.
                try:
                    expected_packets = decode_end_payload(pkt.payload)
                except PacketError:
                    # Fallback: use sequence_number as the count.
                    expected_packets = pkt.sequence_number
                    logger.warning(
                        "END payload could not be decoded; using "
                        "sequence_number=%d as expected count.",
                        expected_packets,
                    )

                logger.info(
                    "END received — expecting %d DATA packets "
                    "(have %d so far).",
                    expected_packets, len(received),
                )

                # If packets are still missing, switch to a shorter
                # post-END timeout so we don't wait forever.
                if len(received) < expected_packets and post_end_timeout > 0:
                    sock.settimeout(post_end_timeout)

    # --- Reconstruction ---------------------------------------------------

    # Handle the empty-file case (0 DATA packets expected).
    if expected_packets is None:
        # END never arrived — infer count from what we received.
        if received:
            expected_packets = max(received.keys()) + 1
            logger.warning(
                "END packet never received. Inferring %d expected "
                "packets from highest sequence number.",
                expected_packets,
            )
        else:
            expected_packets = 0

    missing = [
        seq for seq in range(expected_packets)
        if seq not in received
    ]

    if missing:
        raise RuntimeError(
            f"Cannot reconstruct file: {len(missing)} of "
            f"{expected_packets} DATA packet(s) missing. "
            f"Missing sequence numbers: {missing}"
        )

    # Reassemble in sequence order.
    reconstructed = b"".join(
        received[seq] for seq in range(expected_packets)
    )

    # Write to disk safely.
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(reconstructed)

    file_hash = hashlib.sha256(reconstructed).hexdigest()

    logger.info("Saved: %s (%d bytes)", destination, len(reconstructed))
    logger.info("SHA-256: %s", file_hash)
    if duplicates:
        logger.info("Duplicates ignored: %d", duplicates)

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

    args = parser.parse_args()

    try:
        receive_file(
            args.output,
            args.host,
            args.port,
            args.timeout,
            args.post_end_timeout,
        )
    except (ValueError, TimeoutError, RuntimeError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
