"""
Integration and regression tests for Stop-and-Wait ARQ file transfer.

Author: Prakhar Manish Srivastava
"""

import hashlib
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.packet import (
    MAX_DATAGRAM_SIZE,
    MAX_PAYLOAD_SIZE,
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    create_ack_packet,
    create_packet,
    decode_end_payload,
    decode_packet,
    encode_end_payload,
    encode_packet,
)
from src.reliability import MaxRetriesExceededError, StopAndWaitReceiver
from src.receiver import receive_file
from src.sender import send_file

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===================================================================
# Simulating Impairments (Lossy Proxy)
# ===================================================================

class LossyProxy:
    """A lightweight UDP proxy on localhost that can selectively drop packets or ACKs."""

    def __init__(self, target_port: int, drop_data_seqs=None, drop_ack_seqs=None, drop_end_count=0, drop_end_ack_count=0):
        self.target_port = target_port
        self.drop_data_seqs = set(drop_data_seqs or [])
        self.drop_ack_seqs = set(drop_ack_seqs or [])
        self.drop_end_count = drop_end_count
        self.drop_end_ack_count = drop_end_ack_count

        self.proxy_port = _free_port()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass
        self.sock.bind(("127.0.0.1", self.proxy_port))
        self.sock.settimeout(0.1)

        self.running = True
        self.client_addr = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM_SIZE + 64)
            except (socket.timeout, OSError):
                continue

            try:
                pkt = decode_packet(data)
            except Exception:
                continue

            # Forwarding logic:
            if pkt.packet_type == TYPE_DATA:
                self.client_addr = addr
                if pkt.sequence_number in self.drop_data_seqs:
                    self.drop_data_seqs.remove(pkt.sequence_number)
                    continue  # Drop this DATA packet once!
                self.sock.sendto(data, ("127.0.0.1", self.target_port))

            elif pkt.packet_type == TYPE_END:
                self.client_addr = addr
                if self.drop_end_count > 0:
                    self.drop_end_count -= 1
                    continue  # Drop END packet!
                self.sock.sendto(data, ("127.0.0.1", self.target_port))

            elif pkt.packet_type == TYPE_ACK:
                # Coming from receiver -> forward to client
                if pkt.sequence_number in self.drop_ack_seqs:
                    self.drop_ack_seqs.remove(pkt.sequence_number)
                    continue  # Drop this ACK once!
                if self.drop_end_ack_count > 0:
                    self.drop_end_ack_count -= 1
                    continue  # Drop END ACK once!
                if self.client_addr:
                    self.sock.sendto(data, self.client_addr)

    def close(self):
        self.running = False
        self.sock.close()


# ===================================================================
# End-to-End Stop-and-Wait ARQ Transfer Tests
# ===================================================================

class TestArqTransfers:
    def test_successful_transfer_sha256_equality(self, tmp_path):
        source = tmp_path / "src.bin"
        dest = tmp_path / "dst.bin"

        content = b"Prakhar ARQ reliable transfer payload! " * 80
        source.write_bytes(content)
        expected_hash = _sha256(content)

        port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", port, 10.0, 3.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        result = send_file(str(source), "127.0.0.1", port, arq="stop-and-wait", max_retries=3, initial_rto=0.2)
        rec_thread.join(timeout=10.0)

        assert dest.exists()
        assert _sha256(dest.read_bytes()) == expected_hash
        assert result["sha256"] == expected_hash
        assert result["retransmissions"] == 0

    def test_empty_file_transfer(self, tmp_path):
        source = tmp_path / "empty.bin"
        dest = tmp_path / "empty_dst.bin"
        source.write_bytes(b"")

        port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", port, 5.0, 1.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        result = send_file(str(source), "127.0.0.1", port, arq="stop-and-wait", max_retries=3, initial_rto=0.2)
        rec_thread.join(timeout=5.0)

        assert dest.exists()
        assert dest.read_bytes() == b""
        assert result["total_bytes"] == 0
        assert result["total_packets"] == 0

    def test_partial_chunk_file_transfer(self, tmp_path):
        source = tmp_path / "partial.bin"
        dest = tmp_path / "partial_dst.bin"

        # 2 full MTU chunks + partial 350 bytes
        content = b"X" * (MAX_PAYLOAD_SIZE * 2 + 350)
        source.write_bytes(content)

        port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", port, 10.0, 2.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        result = send_file(str(source), "127.0.0.1", port, arq="stop-and-wait", max_retries=3, initial_rto=0.2)
        rec_thread.join(timeout=10.0)

        assert dest.read_bytes() == content
        assert result["total_packets"] == 3


# ===================================================================
# Simulated Impairment Tests (Loss, Duplicate, Out-of-Order)
# ===================================================================

class TestArqImpairments:
    def test_dropped_data_packet_retransmission(self, tmp_path):
        """Simulate dropped DATA packet seq 1; sender must retransmit it and complete."""
        source = tmp_path / "drop_data.bin"
        dest = tmp_path / "drop_data_dst.bin"

        content = b"Packet 0 chunk payload..." + (b"A" * 1400) + (b"B" * 1400)
        source.write_bytes(content)

        rec_port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", rec_port, 10.0, 3.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        # Drop DATA packet 1 once
        proxy = LossyProxy(rec_port, drop_data_seqs={1})
        try:
            result = send_file(
                str(source), "127.0.0.1", proxy.proxy_port,
                arq="stop-and-wait", max_retries=3, initial_rto=0.2,
            )
            rec_thread.join(timeout=10.0)

            assert dest.read_bytes() == content
            assert result["retransmissions"] >= 1
        finally:
            proxy.close()

    def test_dropped_ack_and_duplicate_data(self, tmp_path):
        """Simulate dropped ACK for seq 0; sender retransmits, receiver absorbs duplicate."""
        source = tmp_path / "drop_ack.bin"
        dest = tmp_path / "drop_ack_dst.bin"

        content = b"Chunk 0" + (b"Y" * 1400)
        source.write_bytes(content)

        rec_port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", rec_port, 10.0, 3.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        # Drop ACK for seq 0 once
        proxy = LossyProxy(rec_port, drop_ack_seqs={0})
        try:
            result = send_file(
                str(source), "127.0.0.1", proxy.proxy_port,
                arq="stop-and-wait", max_retries=3, initial_rto=0.2,
            )
            rec_thread.join(timeout=10.0)

            assert dest.read_bytes() == content
            assert result["retransmissions"] >= 1
        finally:
            proxy.close()

    def test_dropped_end_packet_retransmission(self, tmp_path):
        """Simulate dropped END packet; sender retransmits END packet and completes."""
        source = tmp_path / "drop_end.bin"
        dest = tmp_path / "drop_end_dst.bin"
        content = b"Short transfer"
        source.write_bytes(content)

        rec_port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", rec_port, 10.0, 3.0, "stop-and-wait", 0.5),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        # Drop first END packet
        proxy = LossyProxy(rec_port, drop_end_count=1)
        try:
            result = send_file(
                str(source), "127.0.0.1", proxy.proxy_port,
                arq="stop-and-wait", max_retries=3, initial_rto=0.2,
            )
            rec_thread.join(timeout=10.0)

            assert dest.read_bytes() == content
            assert result["retransmissions"] >= 1
        finally:
            proxy.close()

    def test_retry_exhaustion_bounded_termination(self, tmp_path):
        """Sender targeting a closed port should exhaust retries and raise MaxRetriesExceededError."""
        source = tmp_path / "exhaust.bin"
        source.write_bytes(b"Data that will never be ACKed")

        unreachable_port = _free_port()
        t0 = time.monotonic()
        with pytest.raises(MaxRetriesExceededError):
            send_file(
                str(source), "127.0.0.1", unreachable_port,
                arq="stop-and-wait", max_retries=2, initial_rto=0.1,
            )
        elapsed = time.monotonic() - t0
        # initial 0.1s + retry 1 (0.2s) + retry 2 (0.4s) ~= 0.7s (bounded!)
        assert elapsed < 3.0

    def test_out_of_order_packet_does_not_ack_zero(self):
        """StopAndWaitReceiver must not emit ACK(0) when seq 0 has not yet arrived."""
        receiver = StopAndWaitReceiver()
        deliver, ack = receiver.process_packet(2)
        assert deliver is False
        assert ack is None
        assert receiver.expected_seq == 0

    def test_baseline_none_compatibility(self, tmp_path):
        """Sender and receiver with arq='none' execute unmanaged baseline transfer."""
        source = tmp_path / "none_src.bin"
        dest = tmp_path / "none_dst.bin"
        content = b"Unmanaged UDP blast compatibility test"
        source.write_bytes(content)

        port = _free_port()
        rec_thread = threading.Thread(
            target=receive_file,
            args=(str(dest), "127.0.0.1", port, 5.0, 1.0, "none", 0.0),
            daemon=True,
        )
        rec_thread.start()
        time.sleep(0.2)

        result = send_file(str(source), "127.0.0.1", port, arq="none", packet_interval=0.005)
        rec_thread.join(timeout=5.0)

        assert dest.read_bytes() == content
        assert result["retransmissions"] == 0
