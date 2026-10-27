"""
Adaptive UDP Streaming — UDP Sender
====================================

Authors: Nishtha Bhushan, Prakhar Manish Srivastava, Subham Kumar Sahu
Module: src/sender.py

Reads a file, splits it into sequenced DATA packets, transmits them
over UDP using Adaptive Rate Control or Stop-and-Wait ARQ reliability,
and finishes with an END packet carrying the total DATA-packet count.

Usage (command line)::

    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005
    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005 --arq stop-and-wait --max-retries 5
    python -m src.sender path/to/file.bin --host 127.0.0.1 --port 5005 --mode adaptive
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from src.packet import (
    HEADER_SIZE,
    MAX_DATAGRAM_SIZE,
    MAX_PAYLOAD_SIZE,
    Packet,
    PacketError,
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    create_packet,
    decode_packet,
    encode_end_payload,
    encode_packet,
)
from src.rate_control import (
    AdaptiveRateController,
    RateControlMode,
    RateControllerConfig,
)
from src.reliability import (
    MaxRetriesExceededError,
    RttEstimator,
    StopAndWaitARQ,
)

logger = logging.getLogger(__name__)


@dataclass
class SenderStats:
    """Performance statistics of a streaming transfer."""

    total_bytes: int = 0
    data_packets_sent: int = 0
    retransmissions: int = 0
    acks_received: int = 0
    duration_s: float = 0.0
    goodput_bps: float = 0.0
    packet_loss_ratio: float = 0.0
    final_rate_pps: float = 0.0
    average_rtt_ms: float = 0.0
    average_jitter_ms: float = 0.0
    rate_history: List[dict] = field(default_factory=list)
    sha256: str = ""
    total_packets: int = 0
    rto: Optional[float] = None

    def __getitem__(self, key: str):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: str):
        return hasattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


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
    input_path: str,
    host: str = "127.0.0.1",
    port: int = 5005,
    packet_interval: float = 0.001,
    mode: str = "adaptive",
    controller: Optional[AdaptiveRateController] = None,
    ack_timeout: float = 0.15,
    max_retransmissions: int = 3,
    wait_for_acks: bool = True,
    arq: Optional[str] = None,
    max_retries: int = 5,
    initial_rto: float = 1.0,
    min_rto: Optional[float] = None,
    max_rto: float = 60.0,
) -> SenderStats:
    """Send a file as sequenced UDP packets with rate control or Stop-and-Wait ARQ.

    Parameters:
        input_path:          Path to the input file to send.
        host:                Destination host IP address.
        port:                Destination UDP port (1–65535).
        packet_interval:     Seconds to sleep between packets (≥ 0).
        mode:                Operating mode: 'adaptive' or 'fixed'.
        controller:          Optional custom AdaptiveRateController instance.
        ack_timeout:         Timeout in seconds to await ACKs before retransmitting.
        max_retransmissions: Maximum retransmission attempts per packet.
        wait_for_acks:       Whether to listen for ACKs and perform retransmissions.
        arq:                 Reliability mode: 'stop-and-wait', 'none', or 'adaptive'.
        max_retries:         Maximum retransmission attempts per packet in stop-and-wait mode.
        initial_rto:         Initial retransmission timeout in seconds.
        min_rto:             Minimum retransmission timeout in seconds.
        max_rto:             Maximum retransmission timeout in seconds.

    Returns:
        SenderStats object with metrics, supporting dict-like access.

    Raises:
        FileNotFoundError:       If *input_path* does not exist.
        ValueError:              If parameters are invalid.
        MaxRetriesExceededError: If a packet exceeds max_retries in stop-and-wait ARQ.
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

    if arq is not None and arq not in ("stop-and-wait", "none", "adaptive"):
        raise ValueError(f"Unsupported ARQ mode: {arq!r}. Valid modes are 'stop-and-wait', 'none', and 'adaptive'.")

    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()

    # Compute number of DATA packets. An empty file produces 0 DATA packets.
    total_packets = 0 if len(data) == 0 else (len(data) + MAX_PAYLOAD_SIZE - 1) // MAX_PAYLOAD_SIZE

    # 1. Stop-and-Wait ARQ Mode
    if arq == "stop-and-wait":
        logger.info(
            "Sending %s (%d bytes, %d DATA packet(s), SHA-256: %s, ARQ: %s)",
            path.name, len(data), total_packets, file_hash, arq,
        )

        effective_min_rto = 0.2 if min_rto is None else float(min_rto)
        if initial_rto < effective_min_rto:
            effective_min_rto = float(initial_rto)
        rtt_estimator = RttEstimator(
            initial_rto=initial_rto,
            min_rto=effective_min_rto,
            max_rto=max(max_rto, initial_rto),
        )
        arq_controller = StopAndWaitARQ(rtt_estimator=rtt_estimator, max_retries=max_retries)
        total_retransmissions = 0
        t0 = time.time()

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for seq, offset in enumerate(range(0, max(len(data), 1) if len(data) else 0, MAX_PAYLOAD_SIZE)):
                payload = data[offset : offset + MAX_PAYLOAD_SIZE]
                pkt = create_packet(TYPE_DATA, seq, payload)
                retrans = _send_packet_reliably(sock, pkt, (host, port), arq_controller)
                total_retransmissions += retrans
                logger.info("Delivered DATA seq=%d %d bytes (retries=%d)", seq, len(payload), retrans)
                if packet_interval and seq < total_packets - 1:
                    time.sleep(packet_interval)

            end_payload = encode_end_payload(total_packets)
            end_pkt = create_packet(TYPE_END, total_packets, end_payload)
            retrans = _send_packet_reliably(sock, end_pkt, (host, port), arq_controller)
            total_retransmissions += retrans
            logger.info("Delivered END packet (total_data_packets=%d, retries=%d)", total_packets, retrans)

        duration = max(0.001, time.time() - t0)
        final_rto = arq_controller.rto
        logger.info(
            "Transfer complete: %d bytes, %d DATA packets, %d retransmissions, SHA-256: %s",
            len(data), total_packets, total_retransmissions, file_hash,
        )
        return SenderStats(
            total_bytes=len(data),
            total_packets=total_packets,
            data_packets_sent=total_packets,
            retransmissions=total_retransmissions,
            acks_received=total_packets + 1,
            duration_s=duration,
            goodput_bps=(len(data) * 8.0) / duration,
            packet_loss_ratio=0.0,
            rto=final_rto,
            sha256=file_hash,
        )

    # 2. Baseline Unmanaged (arq='none') Mode
    if arq == "none":
        logger.info(
            "Sending %s (%d bytes, %d DATA packet(s), SHA-256: %s, ARQ: none)",
            path.name, len(data), total_packets, file_hash,
        )
        t0 = time.time()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for seq, offset in enumerate(range(0, max(len(data), 1) if len(data) else 0, MAX_PAYLOAD_SIZE)):
                payload = data[offset : offset + MAX_PAYLOAD_SIZE]
                pkt = create_packet(TYPE_DATA, seq, payload)
                sock.sendto(encode_packet(pkt), (host, port))
                logger.info("Sent DATA seq=%d %d bytes", seq, len(payload))
                if packet_interval and seq < total_packets - 1:
                    time.sleep(packet_interval)

            end_payload = encode_end_payload(total_packets)
            end_pkt = create_packet(TYPE_END, total_packets, end_payload)
            sock.sendto(encode_packet(end_pkt), (host, port))
            logger.info("Sent END packet (total_data_packets=%d)", total_packets)

        duration = max(0.001, time.time() - t0)
        return SenderStats(
            total_bytes=len(data),
            total_packets=total_packets,
            data_packets_sent=total_packets,
            retransmissions=0,
            acks_received=0,
            duration_s=duration,
            goodput_bps=(len(data) * 8.0) / duration,
            packet_loss_ratio=0.0,
            sha256=file_hash,
        )

    # 3. Adaptive Rate Controlled / Sliding Window Mode
    ctrl_mode = RateControlMode(mode.lower())
    if controller is None:
        initial_rate = (1.0 / packet_interval) if packet_interval > 0 else 200.0
        cfg = RateControllerConfig(
            mode=ctrl_mode,
            initial_rate=initial_rate,
            min_rate=10.0,
            max_rate=1000.0,
            min_adjustment_interval_s=0.03,
        )
        controller = AdaptiveRateController(cfg)
    else:
        controller.mode = ctrl_mode

    sent_timestamps: Dict[int, int] = {}
    acked_sequences: Set[int] = set()
    rtt_samples_ms: List[float] = []
    jitter_samples_ms: List[float] = []
    last_rtt_ms: Optional[float] = None
    current_jitter_ms: float = 0.0
    retransmissions_count = 0
    lock = threading.Lock()
    stop_ack_listener = threading.Event()

    def ack_listener_loop(sock: socket.socket):
        nonlocal last_rtt_ms, current_jitter_ms
        sock.settimeout(0.05)
        while not stop_ack_listener.is_set():
            try:
                raw_ack, _ = sock.recvfrom(HEADER_SIZE + 64)
            except (socket.timeout, OSError):
                continue

            try:
                ack_pkt = decode_packet(raw_ack)
            except (PacketError, Exception):
                continue

            if ack_pkt.packet_type == TYPE_ACK:
                seq = ack_pkt.sequence_number
                now_ns = time.monotonic_ns()
                rtt_ms = max(0.0, (now_ns - ack_pkt.timestamp_ns) / 1e6)

                with lock:
                    acked_sequences.add(seq)
                    rtt_samples_ms.append(rtt_ms)

                    if last_rtt_ms is not None:
                        diff = abs(rtt_ms - last_rtt_ms)
                        current_jitter_ms += (diff - current_jitter_ms) / 16.0
                    last_rtt_ms = rtt_ms
                    jitter_samples_ms.append(current_jitter_ms)

                    total_sent = len(sent_timestamps)
                    total_acked = len(acked_sequences)
                    loss_ratio = 0.0
                    if total_sent > 5:
                        loss_ratio = max(0.0, (total_sent - total_acked) / total_sent)

                controller.update_metrics(
                    rtt_ms=rtt_ms,
                    jitter_ms=current_jitter_ms,
                    loss_ratio=loss_ratio,
                )

    start_transfer_time = time.time()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        listener_thread = None
        if wait_for_acks:
            listener_thread = threading.Thread(
                target=ack_listener_loop, args=(sock,), daemon=True
            )
            listener_thread.start()

        chunks: Dict[int, bytes] = {}
        for seq, offset in enumerate(range(0, len(data), MAX_PAYLOAD_SIZE)):
            payload = data[offset : offset + MAX_PAYLOAD_SIZE]
            chunks[seq] = payload

            pkt = create_packet(TYPE_DATA, seq, payload)
            with lock:
                sent_timestamps[seq] = pkt.timestamp_ns

            sock.sendto(encode_packet(pkt), (host, port))
            logger.debug("Sent DATA packet %d, %d bytes", seq, len(payload))

            interval = controller.get_inter_packet_interval()
            if interval > 0:
                time.sleep(interval)

        if wait_for_acks and max_retransmissions > 0:
            for attempt in range(max_retransmissions):
                time.sleep(ack_timeout)

                with lock:
                    missing_seqs = [s for s in range(total_packets) if s not in acked_sequences]

                if not missing_seqs:
                    break

                for seq in missing_seqs:
                    with lock:
                        if seq in acked_sequences:
                            continue

                    payload = chunks[seq]
                    pkt = create_packet(TYPE_DATA, seq, payload)
                    with lock:
                        sent_timestamps[seq] = pkt.timestamp_ns
                        retransmissions_count += 1

                    sock.sendto(encode_packet(pkt), (host, port))
                    logger.debug("Retransmitted DATA packet %d (attempt %d)", seq, attempt + 1)

                    interval = controller.get_inter_packet_interval()
                    if interval > 0:
                        time.sleep(interval)

        if wait_for_acks:
            time.sleep(0.05)
            stop_ack_listener.set()
            if listener_thread is not None:
                listener_thread.join(timeout=1.0)

        end_payload = encode_end_payload(total_packets)
        for _ in range(3 if wait_for_acks else 1):
            end_packet = create_packet(TYPE_END, total_packets, end_payload)
            sock.sendto(encode_packet(end_packet), (host, port))
            time.sleep(0.005)

    duration = max(0.001, time.time() - start_transfer_time)
    goodput_bps = (len(data) * 8.0) / duration
    with lock:
        delivered_count = len(acked_sequences) if wait_for_acks else total_packets
    loss_ratio = max(0.0, (total_packets - delivered_count) / max(1, total_packets))
    avg_rtt = sum(rtt_samples_ms) / max(1, len(rtt_samples_ms))
    avg_jitter = sum(jitter_samples_ms) / max(1, len(jitter_samples_ms))

    stats = SenderStats(
        total_bytes=len(data),
        total_packets=total_packets,
        data_packets_sent=total_packets,
        retransmissions=retransmissions_count,
        acks_received=len(acked_sequences),
        duration_s=duration,
        goodput_bps=goodput_bps,
        packet_loss_ratio=loss_ratio,
        final_rate_pps=controller.get_rate(),
        average_rtt_ms=avg_rtt,
        average_jitter_ms=avg_jitter,
        rate_history=[
            {
                "timestamp": r.timestamp,
                "rate": r.new_rate,
                "rtt_ms": r.smoothed_rtt_ms,
                "jitter_ms": r.smoothed_jitter_ms,
                "loss": r.loss_ratio,
                "reason": r.reason,
            }
            for r in controller.adjustment_history
        ],
        sha256=file_hash,
    )

    logger.info("Transfer finished: %d bytes in %.3fs", len(data), duration)
    logger.info("DATA packets sent: %d (+ %d retransmissions)", total_packets, retransmissions_count)
    logger.info("ACKs received: %d", len(acked_sequences))
    logger.info("Mode: %s | Final Rate: %.1f pkt/s | Goodput: %.1f kbps", mode.upper(), controller.get_rate(), goodput_bps / 1000)
    return stats


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
        "--mode",
        choices=["adaptive", "fixed"],
        default="adaptive",
        help="Transmission mode: 'adaptive' (AIMD) or 'fixed'",
    )
    parser.add_argument(
        "--ack-timeout",
        type=float,
        default=0.15,
        help="ACK timeout in seconds (default: 0.15)",
    )
    parser.add_argument(
        "--max-retransmissions",
        type=int,
        default=3,
        help="Maximum retransmission attempts per unACKed packet",
    )
    parser.add_argument(
        "--no-acks",
        action="store_true",
        help="Disable ACK waiting and retransmissions (open-loop mode)",
    )
    parser.add_argument(
        "--arq",
        choices=["stop-and-wait", "none", "adaptive"],
        default="stop-and-wait",
        help="Reliability mechanism (default: stop-and-wait)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retransmissions per packet in stop-and-wait mode (default: 5)",
    )
    parser.add_argument(
        "--initial-rto",
        type=float,
        default=1.0,
        help="Initial retransmission timeout in seconds (default: 1.0)",
    )

    args = parser.parse_args()

    effective_arq = args.arq
    if "--mode" in sys.argv and "--arq" not in sys.argv:
        effective_arq = "adaptive"

    try:
        send_file(
            args.file,
            host=args.host,
            port=args.port,
            packet_interval=args.interval,
            mode=args.mode,
            ack_timeout=args.ack_timeout,
            max_retransmissions=args.max_retransmissions,
            wait_for_acks=not args.no_acks,
            arq=effective_arq,
            max_retries=args.max_retries,
            initial_rto=args.initial_rto,
        )
    except (FileNotFoundError, ValueError, MaxRetriesExceededError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
