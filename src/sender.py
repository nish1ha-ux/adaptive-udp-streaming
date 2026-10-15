"""
Adaptive UDP Streaming — UDP Sender
====================================

Author: Nishtha Bhushan
Module: src/sender.py

Reads a file, splits it into sequenced DATA packets, transmits them
over UDP, and finishes with an END packet that carries the total
DATA-packet count.

Usage (command line)::

    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005

The sender does **not** implement adaptive rate control or
retransmission — those responsibilities belong to other team members
(Subham and Prakhar respectively).  A simple configurable pacing
interval is provided to avoid flooding the receiver.

Limitations
-----------
* The entire file is read into memory before transmission.
* No flow control or congestion avoidance.
* No retransmission of lost packets.
"""

import argparse
import hashlib
import logging
import socket
import sys
import time
from pathlib import Path

from src.packet import (
    TYPE_DATA,
    TYPE_END,
    MAX_PAYLOAD_SIZE,
    create_packet,
    encode_packet,
    encode_end_payload,
)

logger = logging.getLogger(__name__)


def send_file(
    input_path,
    host="127.0.0.1",
    port=5005,
    packet_interval=0.001,
):
    """Send a file as sequenced UDP packets.

    Parameters:
        input_path:      Path to the file to send.
        host:            Destination IP address.
        port:            Destination UDP port (1–65535).
        packet_interval: Seconds to sleep between DATA packets (≥ 0).

    Returns:
        dict with keys ``total_bytes``, ``total_packets``, ``sha256``.

    Raises:
        FileNotFoundError: If *input_path* does not exist.
        ValueError:        If *port* or *packet_interval* is invalid.
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

    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()

    # Compute number of DATA packets. An empty file produces 0 DATA packets.
    if len(data) == 0:
        total_packets = 0
    else:
        total_packets = (len(data) + MAX_PAYLOAD_SIZE - 1) // MAX_PAYLOAD_SIZE

    logger.info(
        "Sending %s (%d bytes, %d DATA packet(s), SHA-256: %s)",
        path.name, len(data), total_packets, file_hash,
    )

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # Send DATA packets.
        for seq, offset in enumerate(range(0, max(len(data), 1) if len(data) else 0, MAX_PAYLOAD_SIZE)):
            payload = data[offset:offset + MAX_PAYLOAD_SIZE]

            pkt = create_packet(TYPE_DATA, seq, payload)
            sock.sendto(encode_packet(pkt), (host, port))

            logger.info(
                "Sent DATA seq=%d  %d bytes", seq, len(payload),
            )

            if packet_interval and seq < total_packets - 1:
                time.sleep(packet_interval)

        # Send END packet.  Payload carries the total DATA packet count
        # so the receiver knows when it has received every chunk.
        end_payload = encode_end_payload(total_packets)
        end_pkt = create_packet(TYPE_END, total_packets, end_payload)
        sock.sendto(encode_packet(end_pkt), (host, port))

        logger.info("Sent END packet (total_data_packets=%d)", total_packets)

    logger.info(
        "Transfer complete: %d bytes, %d DATA packets, SHA-256: %s",
        len(data), total_packets, file_hash,
    )

    return {
        "total_bytes": len(data),
        "total_packets": total_packets,
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

    args = parser.parse_args()

    try:
        send_file(args.file, args.host, args.port, args.interval)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
