

from __future__ import annotations

import argparse
import logging
import queue
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from src.packet import HEADER_SIZE, PacketError, decode_packet

logger = logging.getLogger("network_emulator")


@dataclass
class EmulatorConfig:
    """Configuration parameters for NetworkEmulator."""

    loss_probability: float = 0.0
    base_delay_ms: float = 0.0
    jitter_ms: float = 0.0
    random_seed: Optional[int] = None
    direction: str = "both"  # "both", "forward", "reverse"

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Validate impairment configuration parameters."""
        if not (0.0 <= self.loss_probability <= 1.0):
            raise ValueError(
                f"loss_probability must be in [0.0, 1.0], got {self.loss_probability}"
            )
        if self.base_delay_ms < 0.0:
            raise ValueError(
                f"base_delay_ms must be non-negative, got {self.base_delay_ms}"
            )
        if self.jitter_ms < 0.0:
            raise ValueError(
                f"jitter_ms must be non-negative, got {self.jitter_ms}"
            )
        if self.direction not in ("both", "forward", "reverse"):
            raise ValueError(
                f"direction must be 'both', 'forward', or 'reverse', got {self.direction!r}"
            )


@dataclass
class PacketAction:
    """Record of an emulation decision applied to a packet."""

    packet_id: int
    packet_type: Optional[int]
    dropped: bool
    delay_ms: float
    timestamp: float
    direction: str


class NetworkEmulator:
    """Core network impairment engine.

    Can be used:
    1. In-process: calling `process_packet(data, direction)` to determine if a packet
       is dropped and what delay is applied.
    2. As an active forwarder: using a background worker thread and queue.
    3. As a standalone UDP relay: proxying packets between sender and receiver.
    """

    def __init__(self, config: Optional[EmulatorConfig] = None):
        self.config = config or EmulatorConfig()
        self._rng = random.Random(self.config.random_seed)
        self._packet_counter = 0
        self._lock = threading.Lock()

        # Action history and counters
        self.action_history: list[PacketAction] = []
        self.total_processed = 0
        self.total_dropped = 0
        self.total_forwarded = 0

        # Background worker queue for scheduled packet delivery
        # Elements are tuples: (release_time, packet_bytes, target_address, callback)
        self._queue: queue.PriorityQueue[
            Tuple[float, int, bytes, Tuple[str, int], Optional[Callable[[bytes, Tuple[str, int]], None]]]
        ] = queue.PriorityQueue()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # Pending task counter for graceful drain
        self._pending_count = 0
        self._pending_lock = threading.Lock()

    def reset_seed(self, seed: Optional[int] = None):
        """Reset the random number generator with a specific seed."""
        with self._lock:
            self.config.random_seed = seed
            self._rng = random.Random(seed)

    def _should_impair(self, direction: str) -> bool:
        """Check if impairment should apply to the given direction."""
        if self.config.direction == "both":
            return True
        return self.config.direction == direction

    def process_packet(
        self,
        raw_data: bytes,
        direction: str = "forward",
    ) -> Tuple[bool, float, Optional[int], Optional[int]]:
        """Evaluate whether to drop or delay a packet.

        Returns:
            (dropped, delay_ms, sequence_number, packet_type)
        """
        with self._lock:
            self._packet_counter += 1
            pkt_id = self._packet_counter

            # Extract sequence number and packet type if valid packet
            seq_num = None
            pkt_type = None
            if len(raw_data) >= HEADER_SIZE:
                try:
                    parsed = decode_packet(raw_data)
                    seq_num = parsed.sequence_number
                    pkt_type = parsed.packet_type
                except (PacketError, Exception):
                    pass

            # Check direction filtering
            if not self._should_impair(direction):
                action = PacketAction(
                    packet_id=pkt_id,
                    packet_type=pkt_type,
                    dropped=False,
                    delay_ms=0.0,
                    timestamp=time.time(),
                    direction=direction,
                )
                self.action_history.append(action)
                self.total_processed += 1
                self.total_forwarded += 1
                return False, 0.0, seq_num, pkt_type

            # Evaluate packet loss
            dropped = False
            if self.config.loss_probability > 0.0:
                if self._rng.random() < self.config.loss_probability:
                    dropped = True

            # Calculate delay and jitter
            delay_ms = 0.0
            if not dropped:
                base = self.config.base_delay_ms
                jitter = 0.0
                if self.config.jitter_ms > 0.0:
                    # Jitter distribution: uniform variation in [-jitter_ms, +jitter_ms]
                    jitter = self._rng.uniform(-self.config.jitter_ms, self.config.jitter_ms)
                effective_delay = base + jitter
                # Effective delay must never be negative
                delay_ms = max(0.0, effective_delay)

            action = PacketAction(
                packet_id=pkt_id,
                packet_type=pkt_type,
                dropped=dropped,
                delay_ms=delay_ms,
                timestamp=time.time(),
                direction=direction,
            )
            self.action_history.append(action)
            self.total_processed += 1
            if dropped:
                self.total_dropped += 1
                logger.debug(
                    "DROPPED packet id=%d seq=%s type=%s dir=%s",
                    pkt_id, seq_num, pkt_type, direction
                )
            else:
                self.total_forwarded += 1
                logger.debug(
                    "FORWARD packet id=%d seq=%s type=%s delay=%.2fms dir=%s",
                    pkt_id, seq_num, pkt_type, delay_ms, direction
                )

            return dropped, delay_ms, seq_num, pkt_type

    def start_worker(self):
        """Start the background worker thread for scheduled delayed forwarding."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name="EmulatorWorker", daemon=True
        )
        self._worker_thread.start()

    def stop_worker(self, drain: bool = False, timeout: float = 2.0):
        """Stop the background worker thread."""
        if drain:
            # Wait until all pending items are processed or timeout expires
            end_time = time.time() + timeout
            while time.time() < end_time:
                with self._pending_lock:
                    if self._pending_count == 0:
                        break
                time.sleep(0.005)

        self._stop_event.set()

        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None

    def schedule_packet(
        self,
        raw_data: bytes,
        target_addr: Tuple[str, int],
        forward_callback: Callable[[bytes, Tuple[str, int]], None],
        direction: str = "forward",
    ) -> bool:
        """Process and schedule a packet for delayed delivery via callback.

        Returns:
            True if packet was scheduled for forwarding, False if dropped.
        """
        dropped, delay_ms, _, _ = self.process_packet(raw_data, direction=direction)
        if dropped:
            return False

        if delay_ms <= 0.0:
            forward_callback(raw_data, target_addr)
            return True

        release_time = time.monotonic() + (delay_ms / 1000.0)
        with self._lock:
            order_id = self._packet_counter
        with self._pending_lock:
            self._pending_count += 1
        self._queue.put((release_time, order_id, raw_data, target_addr, forward_callback))
        return True

    def _worker_loop(self):
        """Worker thread loop releasing scheduled packets at the appropriate monotonic time."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.01)
            except queue.Empty:
                continue

            release_time, order_id, raw_data, target_addr, callback = item
            try:
                now = time.monotonic()
                wait_time = release_time - now
                while wait_time > 0 and not self._stop_event.is_set():
                    sleep_chunk = min(wait_time, 0.005)
                    time.sleep(sleep_chunk)
                    wait_time = release_time - time.monotonic()

                # If wait completed naturally (or draining before stop), execute callback
                if (not self._stop_event.is_set() or wait_time <= 0) and callback is not None:
                    try:
                        callback(raw_data, target_addr)
                    except Exception as exc:
                        logger.error("Error in emulator forwarding callback: %s", exc)
            finally:
                with self._pending_lock:
                    self._pending_count = max(0, self._pending_count - 1)
                self._queue.task_done()


class UDPRelayServer:
    """Standalone UDP Relay that acts as a network bridge between Sender and Receiver.

    Forwards packets arriving at `listen_port` to `(forward_host, forward_port)`
    and relays return packets (ACKs) back to the sender's origin address.
    """

    def __init__(
        self,
        listen_host: str = "127.0.0.1",
        listen_port: int = 5004,
        forward_host: str = "127.0.0.1",
        forward_port: int = 5005,
        config: Optional[EmulatorConfig] = None,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.forward_host = forward_host
        self.forward_port = forward_port
        self.emulator = NetworkEmulator(config)

        self._running = False
        self._sock: Optional[socket.socket] = None
        self._client_addr: Optional[Tuple[str, int]] = None
        self._server_addr: Tuple[str, int] = (self.forward_host, self.forward_port)
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the UDP relay server."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.listen_host, self.listen_port))
        self._sock.settimeout(0.1)

        self.emulator.start_worker()
        self._running = True
        self._thread = threading.Thread(target=self._relay_loop, name="UDPRelayLoop", daemon=True)
        self._thread.start()
        logger.info(
            "UDP Relay listening on %s:%d forwarding to %s:%d",
            self.listen_host, self.listen_port, self.forward_host, self.forward_port
        )

    def stop(self, drain: bool = True, timeout: float = 2.0):
        """Stop the UDP relay server and drain pending packets."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

        self.emulator.stop_worker(drain=drain, timeout=timeout)

        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        logger.info("UDP Relay stopped.")

    def _send_callback(self, data: bytes, target_addr: Tuple[str, int]):
        """Send callback invoked by the emulator worker when scheduled delay elapses."""
        if self._sock is not None:
            try:
                self._sock.sendto(data, target_addr)
            except Exception as exc:
                logger.error("Failed to relay packet to %s: %s", target_addr, exc)

    def _relay_loop(self):
        """Main listening loop for incoming UDP datagrams."""
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            # Distinguish forward path (client -> server) vs reverse path (server -> client)
            if addr == self._server_addr:
                # Return path from receiver to sender (e.g., ACK)
                direction = "reverse"
                if self._client_addr is not None:
                    self.emulator.schedule_packet(
                        data, self._client_addr, self._send_callback, direction=direction
                    )
            else:
                # Forward path from sender to receiver
                self._client_addr = addr
                direction = "forward"
                self.emulator.schedule_packet(
                    data, self._server_addr, self._send_callback, direction=direction
                )


def parse_args():
    parser = argparse.ArgumentParser(description="Network Emulator UDP Relay")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host (default 127.0.0.1)")
    parser.add_argument("--listen-port", type=int, default=5004, help="Listen port (default 5004)")
    parser.add_argument("--forward-host", default="127.0.0.1", help="Forward host (default 127.0.0.1)")
    parser.add_argument("--forward-port", type=int, default=5005, help="Forward port (default 5005)")
    parser.add_argument("--loss", type=float, default=0.0, help="Packet loss probability (0.0 - 1.0)")
    parser.add_argument("--delay", type=float, default=0.0, help="Base delay in ms")
    parser.add_argument("--jitter", type=float, default=0.0, help="Jitter in ms")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--direction", choices=["both", "forward", "reverse"], default="both")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = EmulatorConfig(
        loss_probability=args.loss,
        base_delay_ms=args.delay,
        jitter_ms=args.jitter,
        random_seed=args.seed,
        direction=args.direction,
    )
    relay = UDPRelayServer(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        forward_host=args.forward_host,
        forward_port=args.forward_port,
        config=cfg,
    )
    relay.start()
    print(f"Network Emulator Relay running on {args.listen_host}:{args.listen_port} -> {args.forward_host}:{args.forward_port}")
    print(f"Impairments: loss={args.loss*100:.1f}%, delay={args.delay}ms, jitter={args.jitter}ms, seed={args.seed}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping relay...")
    finally:
        relay.stop()


if __name__ == "__main__":
    main()
