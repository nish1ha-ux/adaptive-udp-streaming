"""
Bandwidth-limited bottleneck for experiments.

Adds a finite-capacity link with a tail-drop queue on top of the existing network
emulator WITHOUT editing ``src/emulator.py``. ``BottleneckEmulator`` subclasses
``NetworkEmulator`` and ``BottleneckRelay`` subclasses ``UDPRelayServer``; the
original random loss, delay and jitter keep working exactly as before.

Model (see ``docs/bandwidth_emulator_plan.md``)
-----------------------------------------------
The forward direction (DATA) is a single server that transmits one packet at a time at
``bandwidth_kbps``. A packet arriving while the link is busy waits in a queue holding at
most ``queue_packets`` packets (besides the one being transmitted); a packet arriving at a full queue is dropped (tail drop).
The reverse direction (ACKs) is not constrained.

    service_s = bytes * 8 / (bandwidth_kbps * 1000)
    start     = max(now, link_free_at)
    ahead     = (start - now) / service_s          # packets in front, incl. the one being sent
    if ahead > queue_packets: drop (congestion)
    else: link_free_at = start + service_s; extra delay = start + service_s - now
"""

import threading
import time
from typing import Optional, Tuple

from src.emulator import EmulatorConfig, NetworkEmulator, UDPRelayServer


class BottleneckEmulator(NetworkEmulator):
    """``NetworkEmulator`` with a bandwidth-limited, finite-queue forward link."""

    def __init__(
        self,
        config: Optional[EmulatorConfig] = None,
        bandwidth_kbps: float = 0.0,
        queue_packets: int = 50,
    ) -> None:
        if bandwidth_kbps < 0:
            raise ValueError(f"bandwidth_kbps must be >= 0, got {bandwidth_kbps}")
        if queue_packets < 1:
            raise ValueError(f"queue_packets must be >= 1, got {queue_packets}")
        super().__init__(config)
        self.bandwidth_kbps = float(bandwidth_kbps)
        self.queue_packets = int(queue_packets)
        self._link_lock = threading.Lock()
        self._link_free_at = 0.0
        self.congestion_drops = 0
        self.max_queue_delay_ms = 0.0

    def process_packet(
        self, raw_data: bytes, direction: str = "forward", now: Optional[float] = None
    ) -> Tuple[bool, float, Optional[int], Optional[int]]:
        """Apply the base impairments, then the bottleneck for forward packets.

        ``now`` (monotonic seconds) can be passed for deterministic tests.
        """
        dropped, delay_ms, seq, ptype = super().process_packet(raw_data, direction)
        if dropped or direction != "forward" or self.bandwidth_kbps == 0:
            return dropped, delay_ms, seq, ptype

        arrival = time.monotonic() if now is None else now
        service_s = len(raw_data) * 8 / (self.bandwidth_kbps * 1000.0)
        with self._link_lock:
            start = max(arrival, self._link_free_at)
            waiting = (start - arrival) / service_s
            # `waiting` counts the packet being transmitted; the queue holds the others.
            if waiting > self.queue_packets + 1e-9:  # epsilon: float rounding
                self.congestion_drops += 1
                # The base class counted this packet as forwarded; correct the counters.
                self.total_forwarded -= 1
                self.total_dropped += 1
                return True, 0.0, seq, ptype
            self._link_free_at = start + service_s
            queue_delay_ms = (start + service_s - arrival) * 1000.0
            self.max_queue_delay_ms = max(self.max_queue_delay_ms, queue_delay_ms)
        return False, delay_ms + queue_delay_ms, seq, ptype


class BottleneckRelay(UDPRelayServer):
    """``UDPRelayServer`` whose emulator includes the bottleneck link."""

    def __init__(
        self,
        *args,
        bandwidth_kbps: float = 0.0,
        queue_packets: int = 50,
        config: Optional[EmulatorConfig] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, config=config, **kwargs)
        self.emulator = BottleneckEmulator(config, bandwidth_kbps, queue_packets)
