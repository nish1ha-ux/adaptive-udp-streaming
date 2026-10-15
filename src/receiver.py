
import argparse
import hashlib
import socket
from pathlib import Path

from src.packet import (
    TYPE_DATA,
    TYPE_END,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    PacketError,
    decode_packet,
)

MAX_DATAGRAM_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE


def receive_file(
    output_path,
    host="127.0.0.1",
    port=5005,
    expected_packets=None,
    timeout=30.0,
):
    """Receive UDP packets and reconstruct the original file."""

    if expected_packets is not None and expected_packets < 0:
        raise ValueError("Expected packet count cannot be negative")

    if timeout <= 0:
        raise ValueError("Timeout must be positive")

    received = {}
    end_received = False

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.bind((host, port))

        print(f"Listening on {host}:{port}")

        while True:
            if (
                end_received
                and expected_packets is not None
                and len(received) == expected_packets
            ):
                break

            if end_received and expected_packets is None:
                break

            try:
                data, address = sock.recvfrom(MAX_DATAGRAM_SIZE)
            except socket.timeout:
                raise TimeoutError(
                    "Timed out waiting for packets. "
                    "Check the sender and expected packet count."
                )

            try:
                packet = decode_packet(data)
            except PacketError as exc:
                print(f"Ignored invalid packet from {address}: {exc}")
                continue

            if packet.packet_type == TYPE_DATA:
                if (
                    expected_packets is not None
                    and packet.sequence_number >= expected_packets
                ):
                    print("Ignored unexpected DATA sequence number")
                    continue

                if packet.sequence_number not in received:
                    received[packet.sequence_number] = packet.payload
                    print(
                        f"Received DATA seq={packet.sequence_number}, "
                        f"bytes={len(packet.payload)}"
                    )
                else:
                    print(
                        f"Ignored duplicate DATA "
                        f"seq={packet.sequence_number}"
                    )

            elif packet.packet_type == TYPE_END:
                end_received = True
                print("Received END packet")

    if expected_packets is None:
        expected_packets = (
            max(received.keys()) + 1 if received else 0
        )

    missing = [
        seq for seq in range(expected_packets)
        if seq not in received
    ]

    if missing:
        raise RuntimeError(
            f"Missing DATA packets: {missing}. "
            "The file cannot be reconstructed completely."
        )

    reconstructed = b"".join(
        received[seq] for seq in range(expected_packets)
    )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(reconstructed)

    digest = hashlib.sha256(reconstructed).hexdigest()

    print(f"Saved reconstructed file: {destination}")
    print(f"Bytes received: {len(reconstructed)}")
    print(f"SHA-256: {digest}")


def main():
    parser = argparse.ArgumentParser(
        description="Receive a file using UDP"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--output", default="received.bin")
    parser.add_argument("--expected-packets", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=30.0)

    args = parser.parse_args()

    receive_file(
        args.output,
        args.host,
        args.port,
        args.expected_packets,
        args.timeout,
    )


if __name__ == "__main__":
    main()

