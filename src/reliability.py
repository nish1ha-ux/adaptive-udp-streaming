"""
Adaptive UDP Streaming — Reliability Core (Stage 2)
===================================================

Author: Prakhar Manish Srivastava
Module: src/reliability.py

Provides socket-independent reliability primitives for UDP streaming:
1. RttEstimator: Jacobson/Karels algorithm (RFC 6298) with Karn's algorithm
   and exponential timer backoff. Uses seconds consistently.
2. StopAndWaitARQ: Sender-side Stop-and-Wait ARQ state controller with timeout
   tracking, matching ACK validation, and retry limits.
3. StopAndWaitReceiver: Receiver-side Stop-and-Wait state helper for duplicate
   filtering and sequence progression.
"""

from typing import Callable, Optional, Tuple
import time


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ReliabilityError(Exception):
    """Base exception for reliability and ARQ errors."""


class MaxRetriesExceededError(ReliabilityError):
    """Raised when retransmissions exceed the configured maximum retry limit."""


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def ns_to_s(ns: int) -> float:
    """Convert nanoseconds (int) to seconds (float)."""
    return ns / 1_000_000_000.0


def s_to_ns(s: float) -> int:
    """Convert seconds (float) to nanoseconds (int)."""
    return int(s * 1_000_000_000)


# ---------------------------------------------------------------------------
# RTT Estimator
# ---------------------------------------------------------------------------

class RttEstimator:
    """Retransmission Timeout (RTO) and RTT Estimator.

    Implements the Jacobson/Karels algorithm (RFC 6298) using seconds consistently:
      - First measurement:
          SRTT   = R
          RTTVAR = R / 2
          RTO    = SRTT + 4 * RTTVAR
      - Subsequent measurements:
          RTTVAR = (1 - beta) * RTTVAR + beta * |SRTT - R|
          SRTT   = (1 - alpha) * SRTT + alpha * R
          RTO    = SRTT + 4 * RTTVAR
      - Karn's Algorithm:
          Samples from retransmitted packets are ignored to avoid ACK ambiguity.
      - Exponential Backoff:
          RTO doubles upon retransmission timeout up to max_rto.
    """

    def __init__(
        self,
        initial_rto: float = 1.0,
        min_rto: float = 0.2,
        max_rto: float = 60.0,
        alpha: float = 0.125,
        beta: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize RttEstimator with bounds and smoothing weights.

        Parameters:
            initial_rto: Initial RTO in seconds before any measurement (default 1.0s).
            min_rto:     Minimum lower bound on RTO in seconds (default 0.2s).
            max_rto:     Maximum upper bound on RTO in seconds (default 60.0s).
            alpha:       SRTT smoothing weight, 0 < alpha < 1 (default 1/8 = 0.125).
            beta:        RTTVAR smoothing weight, 0 < beta < 1 (default 1/4 = 0.25).
            clock:       Callable returning current monotonic time in seconds.
        """
        if min_rto <= 0:
            raise ValueError(f"min_rto must be positive, got {min_rto}")
        if max_rto < min_rto:
            raise ValueError(f"max_rto ({max_rto}) cannot be less than min_rto ({min_rto})")
        if not (min_rto <= initial_rto <= max_rto):
            raise ValueError(
                f"initial_rto ({initial_rto}) must be between min_rto ({min_rto}) and max_rto ({max_rto})"
            )
        if not (0 < alpha < 1):
            raise ValueError(f"alpha must be in (0, 1), got {alpha}")
        if not (0 < beta < 1):
            raise ValueError(f"beta must be in (0, 1), got {beta}")

        self.initial_rto: float = float(initial_rto)
        self.min_rto: float = float(min_rto)
        self.max_rto: float = float(max_rto)
        self.alpha: float = float(alpha)
        self.beta: float = float(beta)
        self.clock: Callable[[], float] = clock

        self.srtt: Optional[float] = None
        self.rttvar: Optional[float] = None
        self.rto: float = self.initial_rto

    def update(self, sample_rtt: float, is_retransmission: bool = False) -> float:
        """Update SRTT, RTTVAR and RTO given an RTT sample in seconds.

        Parameters:
            sample_rtt:        Observed round-trip time in seconds (must be >= 0).
            is_retransmission: Whether the acknowledged packet was retransmitted.
                               If True, Karn's algorithm applies and estimators
                               are NOT updated.

        Returns:
            Current RTO in seconds bounded by [min_rto, max_rto].
        """
        if sample_rtt < 0:
            raise ValueError(f"sample_rtt cannot be negative, got {sample_rtt}")

        if is_retransmission:
            # Karn's algorithm: do not take RTT samples from retransmitted packets
            return self.rto

        if self.srtt is None:
            # First RTT sample
            self.srtt = sample_rtt
            self.rttvar = sample_rtt / 2.0
            self.rto = self.srtt + 4.0 * self.rttvar
        else:
            # Subsequent RTT samples
            self.rttvar = (1.0 - self.beta) * self.rttvar + self.beta * abs(self.srtt - sample_rtt)
            self.srtt = (1.0 - self.alpha) * self.srtt + self.alpha * sample_rtt
            self.rto = self.srtt + 4.0 * self.rttvar

        self.rto = max(self.min_rto, min(self.rto, self.max_rto))
        return self.rto

    def backoff(self) -> float:
        """Apply exponential timeout backoff: RTO = min(RTO * 2, max_rto).

        Returns:
            New backed-off RTO in seconds.
        """
        self.rto = min(self.rto * 2.0, self.max_rto)
        return self.rto

    def reset(self) -> None:
        """Reset estimator to initial unmeasured state."""
        self.srtt = None
        self.rttvar = None
        self.rto = self.initial_rto


# ---------------------------------------------------------------------------
# Stop-and-Wait ARQ Controller (Sender-side)
# ---------------------------------------------------------------------------

class StopAndWaitARQ:
    """Socket-independent Stop-and-Wait ARQ controller.

    Manages single outstanding packet tracking, matching ACK validation,
    timeout detection, exponential backoff, and retry limits.
    """

    def __init__(
        self,
        rtt_estimator: Optional[RttEstimator] = None,
        max_retries: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize controller.

        Parameters:
            rtt_estimator: RttEstimator instance (default creates a standard one).
            max_retries:   Maximum allowed retransmission attempts per packet (>= 0).
            clock:         Callable returning current monotonic time in seconds.
        """
        if max_retries < 0:
            raise ValueError(f"max_retries cannot be negative, got {max_retries}")

        self.clock: Callable[[], float] = clock
        self.rtt_estimator: RttEstimator = (
            rtt_estimator if rtt_estimator is not None else RttEstimator(clock=self.clock)
        )
        self.max_retries: int = int(max_retries)

        self._current_seq: Optional[int] = None
        self._in_flight: bool = False
        self._send_time: Optional[float] = None
        self._timer_start: Optional[float] = None
        self._retransmit_count: int = 0
        self._is_retransmitted: bool = False

    @property
    def is_in_flight(self) -> bool:
        """True if a packet is currently awaiting acknowledgment."""
        return self._in_flight

    @property
    def current_sequence_number(self) -> Optional[int]:
        """Sequence number of the current outstanding packet, or None."""
        return self._current_seq

    @property
    def retries(self) -> int:
        """Number of retransmissions executed for the current packet."""
        return self._retransmit_count

    @property
    def rto(self) -> float:
        """Current retransmission timeout in seconds."""
        return self.rtt_estimator.rto

    def start_packet(self, sequence_number: int, now: Optional[float] = None) -> None:
        """Begin transmission of packet with sequence_number.

        Parameters:
            sequence_number: Non-negative sequence number.
            now:             Optional explicit timestamp in seconds.

        Raises:
            RuntimeError: If a packet is already in flight.
            ValueError:   If sequence_number is invalid.
        """
        if self._in_flight:
            raise RuntimeError(
                f"Cannot start packet seq={sequence_number} while seq={self._current_seq} is still in flight."
            )
        if not isinstance(sequence_number, int) or sequence_number < 0:
            raise ValueError(f"Sequence number must be non-negative integer, got {sequence_number!r}")

        t = float(now if now is not None else self.clock())
        self._current_seq = sequence_number
        self._in_flight = True
        self._send_time = t
        self._timer_start = t
        self._retransmit_count = 0
        self._is_retransmitted = False

    def on_ack_received(self, ack_sequence_number: int, now: Optional[float] = None) -> bool:
        """Process an incoming ACK sequence number.

        Parameters:
            ack_sequence_number: Sequence number in the received ACK.
            now:                 Optional explicit timestamp in seconds.

        Returns:
            True if the ACK matches the outstanding packet and completes it.
            False if duplicate or unexpected (safely ignored, state unchanged).
        """
        if not self._in_flight or self._current_seq is None:
            return False

        if ack_sequence_number != self._current_seq:
            # Duplicate or unexpected ACK from a previous or future packet
            return False

        # Matching ACK confirmed
        t = float(now if now is not None else self.clock())
        sample_rtt = max(0.0, t - (self._send_time if self._send_time is not None else t))

        # Update RTT estimate (respecting Karn's algorithm)
        self.rtt_estimator.update(sample_rtt, is_retransmission=self._is_retransmitted)

        # Clear in-flight state
        self._in_flight = False
        self._current_seq = None
        self._send_time = None
        self._timer_start = None
        # Retain _retransmit_count for post-completion inspection until next start_packet
        self._is_retransmitted = False
        return True

    def check_timeout(self, now: Optional[float] = None) -> bool:
        """Check if the currently outstanding packet has timed out.

        Parameters:
            now: Optional explicit timestamp in seconds.

        Returns:
            True if in flight and elapsed time >= current RTO, False otherwise.
        """
        if not self._in_flight or self._timer_start is None:
            return False

        t = float(now if now is not None else self.clock())
        return (t - self._timer_start) >= self.rtt_estimator.rto

    def handle_timeout(self, now: Optional[float] = None) -> int:
        """Handle expiration of the retransmission timer.

        Increments retry count, marks the packet as retransmitted, resets
        timer start, and backs off RTO exponentially.

        Parameters:
            now: Optional explicit timestamp in seconds.

        Returns:
            The new retransmission count.

        Raises:
            RuntimeError:            If no packet is in flight.
            MaxRetriesExceededError: If retries exceed max_retries.
        """
        if not self._in_flight or self._current_seq is None:
            raise RuntimeError("Cannot handle timeout when no packet is in flight.")

        if self._retransmit_count >= self.max_retries:
            raise MaxRetriesExceededError(
                f"Packet seq={self._current_seq} exceeded maximum retries ({self.max_retries})."
            )

        t = float(now if now is not None else self.clock())
        self._retransmit_count += 1
        self._is_retransmitted = True
        self._timer_start = t
        self.rtt_estimator.backoff()
        return self._retransmit_count

    def remaining_time_to_timeout(self, now: Optional[float] = None) -> float:
        """Return the seconds remaining until the current in-flight packet times out.

        Returns 0.0 if no packet is in flight or if already timed out.
        """
        if not self._in_flight or self._timer_start is None:
            return 0.0
        t = float(now if now is not None else self.clock())
        elapsed = t - self._timer_start
        return max(0.0, self.rto - elapsed)


# ---------------------------------------------------------------------------
# Stop-and-Wait Receiver Helper
# ---------------------------------------------------------------------------

class StopAndWaitReceiver:
    """Socket-independent Stop-and-Wait receiver helper.

    Tracks expected sequence number and determines whether incoming DATA packets
    should be delivered and which ACK sequence number should be returned.
    """

    def __init__(self, initial_expected_seq: int = 0) -> None:
        if not isinstance(initial_expected_seq, int) or initial_expected_seq < 0:
            raise ValueError(f"initial_expected_seq must be non-negative integer, got {initial_expected_seq!r}")
        self.expected_seq: int = initial_expected_seq

    def process_packet(self, sequence_number: int) -> Tuple[bool, Optional[int]]:
        """Process an incoming DATA packet sequence number.

        Parameters:
            sequence_number: Sequence number of received packet.

        Returns:
            A tuple of (should_deliver: bool, ack_to_send: Optional[int]).
            - If matching expected: (True, sequence_number) and increments expected.
            - If duplicate (< expected): (False, sequence_number) to re-ack lost ACK.
            - If unexpected future (> expected): (False, expected - 1 if expected > 0 else None).
              Specifically, does NOT return 0 when sequence 0 has not yet been received.
        """
        if sequence_number == self.expected_seq:
            ack = self.expected_seq
            self.expected_seq += 1
            return (True, ack)
        elif sequence_number < self.expected_seq:
            # Duplicate DATA packet; re-acknowledge it so sender can advance
            return (False, sequence_number)
        else:
            # Out of order packet; cannot accept yet in Stop-and-Wait.
            # Do NOT send ACK(0) if sequence 0 has not been received.
            last_valid = self.expected_seq - 1 if self.expected_seq > 0 else None
            return (False, last_valid)
