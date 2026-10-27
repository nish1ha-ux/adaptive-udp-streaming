

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from src.packet import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    PacketError,
    create_packet,
    decode_packet,
    encode_packet,
)
from src.rate_control import (
    AdaptiveRateController,
    RateControlMode,
    RateControllerConfig,
)

logger = logging.getLogger("sender")


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
) -> SenderStats:
    """Send a file as sequenced UDP packets with rate control and reliability.

    Parameters:
        input_path: Path to the input file to send.
        host: Destination host IP address.
        port: Destination UDP port.
        packet_interval: Initial inter-packet interval in seconds.
        mode: Operating mode: 'adaptive' or 'fixed'.
        controller: Optional custom AdaptiveRateController instance.
        ack_timeout: Timeout in seconds to await ACKs before retransmitting.
        max_retransmissions: Maximum retransmission attempts per packet.
        wait_for_acks: Whether to listen for ACKs and perform retransmissions.
    """
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")

    if packet_interval < 0:
        raise ValueError("Packet interval cannot be negative")

    data = path.read_bytes()
    packet_count = (len(data) + MAX_PAYLOAD_SIZE - 1) // MAX_PAYLOAD_SIZE

    # Initialize rate controller if not provided
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

    # Tracking data structures
    sent_timestamps: Dict[int, int] = {}  # seq -> sent_timestamp_ns
    acked_sequences: Set[int] = set()
    rtt_samples_ms: List[float] = []
    jitter_samples_ms: List[float] = []
    last_rtt_ms: Optional[float] = None
    current_jitter_ms: float = 0.0
    retransmissions_count = 0
    lock = threading.Lock()
    stop_ack_listener = threading.Event()

    def ack_listener_loop(sock: socket.socket):
        """Background thread listening for ACK feedback from receiver."""
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

                    # RFC 3550 Jitter calculation: D(i, j) = |R_j - R_i| - (S_j - S_i)
                    if last_rtt_ms is not None:
                        diff = abs(rtt_ms - last_rtt_ms)
                        current_jitter_ms += (diff - current_jitter_ms) / 16.0
                    last_rtt_ms = rtt_ms
                    jitter_samples_ms.append(current_jitter_ms)

                    # Calculate recent packet loss ratio
                    total_sent = len(sent_timestamps)
                    total_acked = len(acked_sequences)
                    loss_ratio = 0.0
                    if total_sent > 5:
                        loss_ratio = max(0.0, (total_sent - total_acked) / total_sent)

                # Feed metrics to rate controller
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

        # Phase A: Initial transmission of all data chunks
        chunks: Dict[int, bytes] = {}
        for seq, offset in enumerate(range(0, len(data), MAX_PAYLOAD_SIZE)):
            payload = data[offset : offset + MAX_PAYLOAD_SIZE]
            chunks[seq] = payload

            pkt = create_packet(TYPE_DATA, seq, payload)
            with lock:
                sent_timestamps[seq] = pkt.timestamp_ns

            sock.sendto(encode_packet(pkt), (host, port))
            print(f"Sent DATA packet {seq}, {len(payload)} bytes")

            # Rate pacing: calculate interval from controller
            interval = controller.get_inter_packet_interval()
            if interval > 0:
                time.sleep(interval)

        # Phase B: Retransmission of unacknowledged packets if wait_for_acks is enabled
        if wait_for_acks and max_retransmissions > 0:
            for attempt in range(max_retransmissions):
                # Wait briefly for in-flight ACKs
                time.sleep(ack_timeout)

                with lock:
                    missing_seqs = [s for s in range(packet_count) if s not in acked_sequences]

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
                    print(f"Retransmitted DATA packet {seq} (attempt {attempt + 1})")

                    interval = controller.get_inter_packet_interval()
                    if interval > 0:
                        time.sleep(interval)

        # Stop ACK listener thread
        if wait_for_acks:
            time.sleep(0.05)
            stop_ack_listener.set()
            if listener_thread is not None:
                listener_thread.join(timeout=1.0)

        # Phase C: Send END packet (send multiple times to ensure arrival under loss)
        for _ in range(3 if wait_for_acks else 1):
            end_packet = create_packet(TYPE_END, packet_count, b"")
            sock.sendto(encode_packet(end_packet), (host, port))
            time.sleep(0.005)

    duration = max(0.001, time.time() - start_transfer_time)
    goodput_bps = (len(data) * 8.0) / duration
    with lock:
        delivered_count = len(acked_sequences) if wait_for_acks else packet_count
    loss_ratio = max(0.0, (packet_count - delivered_count) / max(1, packet_count))
    avg_rtt = sum(rtt_samples_ms) / max(1, len(rtt_samples_ms))
    avg_jitter = sum(jitter_samples_ms) / max(1, len(jitter_samples_ms))

    stats = SenderStats(
        total_bytes=len(data),
        data_packets_sent=packet_count,
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
    )

    print(f"Transfer finished: {len(data)} bytes in {duration:.3f}s")
    print(f"DATA packets sent: {packet_count} (+ {retransmissions_count} retransmissions)")
    print(f"ACKs received: {len(acked_sequences)}")
    print(f"Mode: {mode.upper()} | Final Rate: {controller.get_rate():.1f} pkt/s | Goodput: {goodput_bps/1000:.1f} kbps")
    print("END packet sent")
    return stats


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
        help="Initial or fixed delay between packets in seconds",
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
        help="ACK timeout in seconds",
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

    args = parser.parse_args()

    send_file(
        args.file,
        host=args.host,
        port=args.port,
        packet_interval=args.interval,
        mode=args.mode,
        ack_timeout=args.ack_timeout,
        max_retransmissions=args.max_retransmissions,
        wait_for_acks=not args.no_acks,
    )


if __name__ == "__main__":
    main()
