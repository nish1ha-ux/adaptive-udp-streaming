
import argparse
import socket
import time
from pathlib import Path

from src.packet import (
    TYPE_DATA,
    TYPE_END,
    MAX_PAYLOAD_SIZE,
    create_packet,
    encode_packet,
)


def send_file(
    input_path,
    host="127.0.0.1",
    port=5005,
    packet_interval=0.001,
):
    """Send a file as sequenced UDP packets."""
    path = Path(input_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")

    if packet_interval < 0:
        raise ValueError("Packet interval cannot be negative")

    data = path.read_bytes()
    packet_count = (
        len(data) + MAX_PAYLOAD_SIZE - 1
    ) // MAX_PAYLOAD_SIZE

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for sequence_number, offset in enumerate(
            range(0, len(data), MAX_PAYLOAD_SIZE)
        ):
            payload = data[offset:offset + MAX_PAYLOAD_SIZE]

            packet = create_packet(
                TYPE_DATA,
                sequence_number,
                payload,
            )

            sock.sendto(encode_packet(packet), (host, port))

            print(
                f"Sent DATA packet {sequence_number}, "
                f"{len(payload)} bytes"
            )

            if packet_interval:
                time.sleep(packet_interval)

        # END uses the next sequence number.
        end_packet = create_packet(
            TYPE_END,
            packet_count,
            b"",
        )
        sock.sendto(encode_packet(end_packet), (host, port))

    print(f"Transfer finished: {len(data)} bytes")
    print(f"DATA packets sent: {packet_count}")
    print("END packet sent")


def main():
    parser = argparse.ArgumentParser(
        description="Send a file using Adaptive UDP Streaming"
    )
    parser.add_argument("file", help="Input file path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument(
        "--interval",
        type=float,
        default=0.001,
        help="Delay between packets in seconds",
    )

    args = parser.parse_args()

    send_file(
        args.file,
        args.host,
        args.port,
        args.interval,
    )


if __name__ == "__main__":
    main()

