"""
Tests for src/sender.py and src/receiver.py — UDP file-transfer integration.

Author: Nishtha Bhushan

These tests launch sender and receiver as subprocesses to verify
end-to-end file transfer.  Each test binds to an ephemeral port to
avoid conflicts, and all subprocesses are cleaned up in ``finally``
blocks.

Note: tests exercise the public protocol and command-line interface.
Adaptive rate control, ACK/retransmission, and network emulation are
outside this module's scope and are not tested here.
"""

import hashlib
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.packet import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    TYPE_DATA,
    TYPE_END,
    create_packet,
    decode_packet,
    encode_end_payload,
    encode_packet,
)

ROOT = Path(__file__).resolve().parents[1]


# ===================================================================
# Helpers
# ===================================================================

def _free_port():
    """Return an available UDP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _start_receiver(port, output, timeout=10, post_end_timeout=3):
    """Start the receiver as a subprocess and return the Popen handle."""
    return subprocess.Popen(
        [
            sys.executable, "-m", "src.receiver",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--output", str(output),
            "--timeout", str(timeout),
            "--post-end-timeout", str(post_end_timeout),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_sender(port, source, interval=0.005, timeout=10):
    """Run the sender to completion and return the CompletedProcess."""
    return subprocess.run(
        [
            sys.executable, "-m", "src.sender",
            str(source),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--interval", str(interval),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ===================================================================
# Basic end-to-end transfer
# ===================================================================

class TestFileTransfer:
    """End-to-end multi-packet file transfer tests."""

    def test_multi_packet_transfer(self, tmp_path):
        """Send a file that requires multiple DATA packets and verify
        bit-for-bit reconstruction and SHA-256 match."""
        source = tmp_path / "source.bin"
        destination = tmp_path / "received.bin"

        # 5120 bytes → 4 DATA packets at 1400-byte chunks.
        original = bytes(range(256)) * 20
        source.write_bytes(original)

        port = _free_port()
        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)

            sender = _run_sender(port, source)
            assert sender.returncode == 0, sender.stderr

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr

            assert destination.exists()
            reconstructed = destination.read_bytes()
            assert reconstructed == original
            assert _sha256(reconstructed) == _sha256(original)
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_single_packet_transfer(self, tmp_path):
        """A file smaller than MAX_PAYLOAD_SIZE should require exactly
        one DATA packet."""
        source = tmp_path / "small.txt"
        destination = tmp_path / "received.txt"

        original = b"Small file content"
        source.write_bytes(original)

        port = _free_port()
        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)
            sender = _run_sender(port, source)
            assert sender.returncode == 0, sender.stderr

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr

            assert destination.read_bytes() == original
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_empty_file_transfer(self, tmp_path):
        """An empty file should produce 0 DATA packets and still
        reconstruct correctly."""
        source = tmp_path / "empty.bin"
        destination = tmp_path / "received.bin"

        source.write_bytes(b"")

        port = _free_port()
        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)
            sender = _run_sender(port, source)
            assert sender.returncode == 0, sender.stderr

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr

            assert destination.exists()
            assert destination.read_bytes() == b""
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_exact_payload_boundary(self, tmp_path):
        """A file whose size is an exact multiple of MAX_PAYLOAD_SIZE."""
        source = tmp_path / "exact.bin"
        destination = tmp_path / "received.bin"

        original = b"\xCC" * (MAX_PAYLOAD_SIZE * 3)
        source.write_bytes(original)

        port = _free_port()
        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)
            sender = _run_sender(port, source)
            assert sender.returncode == 0, sender.stderr

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr

            assert destination.read_bytes() == original
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()


# ===================================================================
# SHA-256 integrity verification
# ===================================================================

class TestIntegrity:
    def test_sha256_matches(self, tmp_path):
        """The SHA-256 hash printed by the receiver should match the
        original file's hash."""
        source = tmp_path / "hash_check.bin"
        destination = tmp_path / "received.bin"

        original = b"Integrity check payload " * 100
        source.write_bytes(original)
        expected_hash = _sha256(original)

        port = _free_port()
        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)
            sender = _run_sender(port, source)
            assert sender.returncode == 0, sender.stderr

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr

            # Verify reconstructed file matches.
            assert _sha256(destination.read_bytes()) == expected_hash

            # The receiver should log the hash.
            combined_output = (stdout or "") + (stderr or "")
            assert expected_hash in combined_output
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()


# ===================================================================
# Duplicate and out-of-order packet handling (unit-level)
# ===================================================================

class TestDuplicateAndReorder:
    """These tests send raw packets directly to the receiver socket
    to exercise duplicate detection and out-of-order reassembly."""

    def test_duplicate_packets_ignored(self, tmp_path):
        """Sending the same DATA packet twice should not corrupt
        the output."""
        destination = tmp_path / "received.bin"
        port = _free_port()

        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)

            payload = b"duplicate test"
            total = 1
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                pkt = create_packet(TYPE_DATA, 0, payload)
                encoded = encode_packet(pkt)

                # Send twice.
                sock.sendto(encoded, ("127.0.0.1", port))
                sock.sendto(encoded, ("127.0.0.1", port))

                # Send END.
                end_payload = encode_end_payload(total)
                end_pkt = create_packet(TYPE_END, total, end_payload)
                sock.sendto(encode_packet(end_pkt), ("127.0.0.1", port))

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr
            assert destination.read_bytes() == payload
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_out_of_order_reassembly(self, tmp_path):
        """Packets arriving out of order should still be reassembled
        in the correct sequence."""
        destination = tmp_path / "received.bin"
        port = _free_port()

        receiver = _start_receiver(port, destination)

        try:
            time.sleep(0.5)

            chunks = [b"AAAA", b"BBBB", b"CCCC"]
            total = len(chunks)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                # Send in reverse order.
                for seq in reversed(range(total)):
                    pkt = create_packet(TYPE_DATA, seq, chunks[seq])
                    sock.sendto(
                        encode_packet(pkt), ("127.0.0.1", port),
                    )
                    time.sleep(0.01)

                # Send END.
                end_payload = encode_end_payload(total)
                end_pkt = create_packet(TYPE_END, total, end_payload)
                sock.sendto(encode_packet(end_pkt), ("127.0.0.1", port))

            stdout, stderr = receiver.communicate(timeout=15)
            assert receiver.returncode == 0, stdout + stderr
            assert destination.read_bytes() == b"AAAABBBBCCCC"
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()


# ===================================================================
# Timeout and missing packets
# ===================================================================

class TestTimeout:
    def test_receiver_timeout_no_sender(self, tmp_path):
        """Receiver should timeout gracefully when no sender runs."""
        destination = tmp_path / "received.bin"
        port = _free_port()

        receiver = _start_receiver(port, destination, timeout=2)

        try:
            stdout, stderr = receiver.communicate(timeout=10)
            assert receiver.returncode != 0
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_missing_packet_detected(self, tmp_path):
        """If a DATA packet is missing, the receiver should report
        it after the post-END timeout."""
        destination = tmp_path / "received.bin"
        port = _free_port()

        receiver = _start_receiver(
            port, destination, timeout=5, post_end_timeout=1,
        )

        try:
            time.sleep(0.5)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                # Send only seq=0, skip seq=1.
                pkt0 = create_packet(TYPE_DATA, 0, b"chunk0")
                sock.sendto(encode_packet(pkt0), ("127.0.0.1", port))

                # END says 2 packets expected.
                end_payload = encode_end_payload(2)
                end_pkt = create_packet(TYPE_END, 2, end_payload)
                sock.sendto(encode_packet(end_pkt), ("127.0.0.1", port))

            stdout, stderr = receiver.communicate(timeout=15)
            # Should fail due to missing packet.
            assert receiver.returncode != 0
            combined = (stdout or "") + (stderr or "")
            assert "missing" in combined.lower() or "Missing" in combined
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()


# ===================================================================
# Sender input validation
# ===================================================================

class TestSenderValidation:
    def test_nonexistent_file(self, tmp_path):
        """Sender should exit with an error for a missing file."""
        result = subprocess.run(
            [
                sys.executable, "-m", "src.sender",
                str(tmp_path / "nonexistent.bin"),
                "--host", "127.0.0.1",
                "--port", "9999",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode != 0
