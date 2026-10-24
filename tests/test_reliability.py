"""
Unit tests for src/reliability.py (RTT Estimator, Karn's algorithm, and Stop-and-Wait ARQ).

Author: Prakhar Manish Srivastava
"""

import math
import pytest

from src.reliability import (
    MaxRetriesExceededError,
    ReliabilityError,
    RttEstimator,
    StopAndWaitARQ,
    StopAndWaitReceiver,
    ns_to_s,
    s_to_ns,
)


# ===================================================================
# Helper clock for deterministic testing
# ===================================================================

class MockClock:
    def __init__(self, start_time: float = 100.0) -> None:
        self._current_time = float(start_time)

    def __call__(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> float:
        self._current_time += float(seconds)
        return self._current_time

    def set(self, time_val: float) -> None:
        self._current_time = float(time_val)


# ===================================================================
# RttEstimator Tests
# ===================================================================

class TestRttEstimator:
    """Tests for Jacobson/Karels RTT estimation and Karn's algorithm."""

    def test_default_initialization(self):
        est = RttEstimator()
        assert est.initial_rto == 1.0
        assert est.min_rto == 0.2
        assert est.max_rto == 60.0
        assert est.alpha == 0.125
        assert est.beta == 0.25
        assert est.srtt is None
        assert est.rttvar is None
        assert est.rto == 1.0

    def test_custom_initialization(self):
        est = RttEstimator(
            initial_rto=2.0,
            min_rto=0.5,
            max_rto=30.0,
            alpha=0.2,
            beta=0.3,
        )
        assert est.initial_rto == 2.0
        assert est.min_rto == 0.5
        assert est.max_rto == 30.0
        assert est.alpha == 0.2
        assert est.beta == 0.3
        assert est.rto == 2.0

    def test_invalid_parameters_raise(self):
        # min_rto <= 0
        with pytest.raises(ValueError, match="min_rto must be positive"):
            RttEstimator(min_rto=0.0)
        with pytest.raises(ValueError, match="min_rto must be positive"):
            RttEstimator(min_rto=-1.0)

        # max_rto < min_rto
        with pytest.raises(ValueError, match="max_rto .* cannot be less than min_rto"):
            RttEstimator(min_rto=1.0, max_rto=0.5)

        # initial_rto out of bounds
        with pytest.raises(ValueError, match="initial_rto .* must be between"):
            RttEstimator(initial_rto=0.1, min_rto=0.2, max_rto=10.0)
        with pytest.raises(ValueError, match="initial_rto .* must be between"):
            RttEstimator(initial_rto=15.0, min_rto=0.2, max_rto=10.0)

        # invalid alpha
        with pytest.raises(ValueError, match="alpha must be in"):
            RttEstimator(alpha=0.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            RttEstimator(alpha=1.0)

        # invalid beta
        with pytest.raises(ValueError, match="beta must be in"):
            RttEstimator(beta=0.0)
        with pytest.raises(ValueError, match="beta must be in"):
            RttEstimator(beta=1.0)

    def test_first_rtt_sample(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)
        sample = 0.2
        rto = est.update(sample)

        # RFC 6298: SRTT = R, RTTVAR = R/2, RTO = SRTT + 4 * RTTVAR
        assert est.srtt == pytest.approx(0.2)
        assert est.rttvar == pytest.approx(0.1)
        expected_rto = 0.2 + 4.0 * 0.1  # 0.6
        assert est.rto == pytest.approx(expected_rto)
        assert rto == pytest.approx(expected_rto)

    def test_subsequent_rtt_samples(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0, alpha=0.125, beta=0.25)

        # Sample 1: 0.2s
        est.update(0.2)
        assert est.srtt == pytest.approx(0.2)
        assert est.rttvar == pytest.approx(0.1)
        assert est.rto == pytest.approx(0.6)

        # Sample 2: 0.3s
        # RTTVAR = (1 - 0.25) * 0.1 + 0.25 * |0.2 - 0.3| = 0.075 + 0.025 = 0.1
        # SRTT   = (1 - 0.125) * 0.2 + 0.125 * 0.3 = 0.175 + 0.0375 = 0.2125
        # RTO    = 0.2125 + 4 * 0.1 = 0.6125
        est.update(0.3)
        assert est.rttvar == pytest.approx(0.1)
        assert est.srtt == pytest.approx(0.2125)
        assert est.rto == pytest.approx(0.6125)

        # Sample 3: 0.15s
        # RTTVAR = 0.75 * 0.1 + 0.25 * |0.2125 - 0.15| = 0.075 + 0.015625 = 0.090625
        # SRTT   = 0.875 * 0.2125 + 0.125 * 0.15 = 0.1859375 + 0.01875 = 0.2046875
        # RTO    = 0.2046875 + 4 * 0.090625 = 0.5671875
        est.update(0.15)
        assert est.rttvar == pytest.approx(0.090625)
        assert est.srtt == pytest.approx(0.2046875)
        assert est.rto == pytest.approx(0.5671875)

    def test_rto_clamped_to_min_bound(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)
        # Very tiny RTT sample: 0.001s -> raw RTO = 0.001 + 4*0.0005 = 0.003s
        est.update(0.001)
        assert est.srtt == pytest.approx(0.001)
        assert est.rttvar == pytest.approx(0.0005)
        assert est.rto == pytest.approx(0.2)  # Clamped to min_rto

    def test_rto_clamped_to_max_bound(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=5.0)
        # Giant RTT sample: 10.0s -> raw RTO = 10 + 4*5 = 30s
        est.update(10.0)
        assert est.rto == pytest.approx(5.0)  # Clamped to max_rto

    def test_karns_algorithm_ignores_retransmitted_packets(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)

        # Established RTT
        est.update(0.2)
        saved_srtt = est.srtt
        saved_rttvar = est.rttvar
        saved_rto = est.rto

        # Retransmission sample arrives: Karn's algorithm specifies it MUST be ignored
        returned_rto = est.update(2.5, is_retransmission=True)
        assert returned_rto == pytest.approx(saved_rto)
        assert est.srtt == pytest.approx(saved_srtt)
        assert est.rttvar == pytest.approx(saved_rttvar)
        assert est.rto == pytest.approx(saved_rto)

    def test_karns_algorithm_when_first_sample_is_retransmission(self):
        est = RttEstimator(initial_rto=1.5, min_rto=0.2, max_rto=60.0)
        est.update(0.5, is_retransmission=True)
        # Should still be uninitialized
        assert est.srtt is None
        assert est.rttvar is None
        assert est.rto == pytest.approx(1.5)

    def test_exponential_backoff_and_cap(self):
        est = RttEstimator(initial_rto=1.0, min_rto=0.2, max_rto=5.0)
        assert est.backoff() == pytest.approx(2.0)
        assert est.backoff() == pytest.approx(4.0)
        assert est.backoff() == pytest.approx(5.0)  # Capped at max_rto
        assert est.backoff() == pytest.approx(5.0)  # Stays at cap

    def test_negative_sample_raises_value_error(self):
        est = RttEstimator()
        with pytest.raises(ValueError, match="cannot be negative"):
            est.update(-0.001)

    def test_zero_sample_is_valid(self):
        est = RttEstimator(min_rto=0.2)
        est.update(0.0)
        assert est.srtt == pytest.approx(0.0)
        assert est.rttvar == pytest.approx(0.0)
        assert est.rto == pytest.approx(0.2)

    def test_reset(self):
        est = RttEstimator(initial_rto=1.0)
        est.update(0.4)
        est.backoff()
        est.reset()
        assert est.srtt is None
        assert est.rttvar is None
        assert est.rto == pytest.approx(1.0)


# ===================================================================
# StopAndWaitARQ Tests
# ===================================================================

class TestStopAndWaitARQ:
    """Tests for Stop-and-Wait ARQ state controller."""

    def test_initial_state(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock, max_retries=3)
        assert not arq.is_in_flight
        assert arq.current_sequence_number is None
        assert arq.retries == 0
        assert arq.rto == pytest.approx(1.0)
        assert not arq.check_timeout()

    def test_invalid_max_retries_raises(self):
        with pytest.raises(ValueError, match="max_retries cannot be negative"):
            StopAndWaitARQ(max_retries=-1)

    def test_start_packet_success(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        arq.start_packet(0)
        assert arq.is_in_flight
        assert arq.current_sequence_number == 0
        assert arq.retries == 0

    def test_start_packet_while_in_flight_raises(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        arq.start_packet(0)
        with pytest.raises(RuntimeError, match="while seq=0 is still in flight"):
            arq.start_packet(1)

    def test_start_packet_invalid_sequence_number(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        with pytest.raises(ValueError, match="Sequence number must be non-negative"):
            arq.start_packet(-1)
        with pytest.raises(ValueError, match="Sequence number must be non-negative"):
            arq.start_packet("0")

    def test_matching_ack_completes_packet_and_updates_rtt(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        arq.start_packet(0, now=100.0)

        # ACK arrives 100ms later
        result = arq.on_ack_received(0, now=100.1)
        assert result is True
        assert not arq.is_in_flight
        assert arq.current_sequence_number is None
        assert arq.retries == 0

        # RTT estimator updated with sample = 0.1s: RTO = 0.1 + 4*0.05 = 0.3s
        assert arq.rtt_estimator.srtt == pytest.approx(0.1)
        assert arq.rtt_estimator.rttvar == pytest.approx(0.05)
        assert arq.rto == pytest.approx(0.3)

    def test_duplicate_or_unexpected_ack_ignored(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        arq.start_packet(1, now=100.0)

        # Duplicate ACK from old packet 0 arrives
        assert arq.on_ack_received(0, now=100.05) is False
        assert arq.is_in_flight
        assert arq.current_sequence_number == 1

        # Premature ACK for future packet 2 arrives
        assert arq.on_ack_received(2, now=100.06) is False
        assert arq.is_in_flight
        assert arq.current_sequence_number == 1

        # Correct ACK arrives
        assert arq.on_ack_received(1, now=100.1) is True
        assert not arq.is_in_flight

    def test_ack_when_no_packet_in_flight_ignored(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        assert arq.on_ack_received(0, now=100.0) is False

    def test_check_timeout(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock)
        assert not arq.check_timeout(now=100.0)
        assert arq.remaining_time_to_timeout(now=100.0) == pytest.approx(0.0)

        arq.start_packet(0, now=100.0)  # RTO is 1.0s
        assert not arq.check_timeout(now=100.5)
        assert arq.remaining_time_to_timeout(now=100.5) == pytest.approx(0.5)
        assert not arq.check_timeout(now=100.99)
        assert arq.remaining_time_to_timeout(now=100.99) == pytest.approx(0.01)
        assert arq.check_timeout(now=101.0)
        assert arq.remaining_time_to_timeout(now=101.0) == pytest.approx(0.0)
        assert arq.check_timeout(now=101.5)
        assert arq.remaining_time_to_timeout(now=101.5) == pytest.approx(0.0)

    def test_handle_timeout_backs_off_rto_and_increments_retries(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock, max_retries=3)
        arq.start_packet(0, now=100.0)  # initial RTO = 1.0s

        # First timeout at t=101.0
        retries = arq.handle_timeout(now=101.0)
        assert retries == 1
        assert arq.retries == 1
        assert arq.rto == pytest.approx(2.0)  # Backed off 1.0 -> 2.0

        # Now timer base is 101.0. At t=102.5, elapsed is 1.5s (< 2.0s RTO) -> Not timed out
        assert not arq.check_timeout(now=102.5)
        # At t=103.0, elapsed is 2.0s (== RTO) -> Timed out!
        assert arq.check_timeout(now=103.0)

        # Second timeout
        retries = arq.handle_timeout(now=103.0)
        assert retries == 2
        assert arq.rto == pytest.approx(4.0)

    def test_karns_rule_on_retransmitted_packet(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock, max_retries=3)
        arq.start_packet(0, now=100.0)

        # Timeout occurs -> packet marked as retransmitted
        arq.handle_timeout(now=101.0)
        assert arq._is_retransmitted is True

        # When ACK arrives for this retransmitted packet, RTT estimators must NOT be updated
        assert arq.on_ack_received(0, now=101.5) is True
        assert not arq.is_in_flight
        assert arq.rtt_estimator.srtt is None  # Karn's rule: sample discarded!

    def test_max_retries_exceeded_raises_error(self):
        clock = MockClock(100.0)
        arq = StopAndWaitARQ(clock=clock, max_retries=2)
        arq.start_packet(0, now=100.0)

        # Retry 1
        arq.handle_timeout(now=101.0)
        assert arq.retries == 1

        # Retry 2 (matches max_retries)
        arq.handle_timeout(now=103.0)
        assert arq.retries == 2

        # Retry 3 exceeds max_retries -> raises MaxRetriesExceededError
        with pytest.raises(MaxRetriesExceededError, match=r"exceeded maximum retries \(2\)"):
            arq.handle_timeout(now=107.0)

    def test_handle_timeout_without_packet_in_flight_raises(self):
        arq = StopAndWaitARQ()
        with pytest.raises(RuntimeError, match="Cannot handle timeout when no packet is in flight"):
            arq.handle_timeout()


# ===================================================================
# StopAndWaitReceiver Tests
# ===================================================================

class TestStopAndWaitReceiver:
    """Tests for receiver-side Stop-and-Wait helper."""

    def test_initial_state(self):
        rec = StopAndWaitReceiver()
        assert rec.expected_seq == 0

        rec_custom = StopAndWaitReceiver(initial_expected_seq=5)
        assert rec_custom.expected_seq == 5

    def test_invalid_initial_seq_raises(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            StopAndWaitReceiver(initial_expected_seq=-1)

    def test_in_order_packet_processing(self):
        rec = StopAndWaitReceiver()
        # Packet 0
        deliver, ack = rec.process_packet(0)
        assert deliver is True
        assert ack == 0
        assert rec.expected_seq == 1

        # Packet 1
        deliver, ack = rec.process_packet(1)
        assert deliver is True
        assert ack == 1
        assert rec.expected_seq == 2

    def test_duplicate_packet_processing(self):
        rec = StopAndWaitReceiver()
        rec.process_packet(0)
        assert rec.expected_seq == 1

        # Duplicate of packet 0 arrives again (previous ACK was dropped)
        deliver, ack = rec.process_packet(0)
        assert deliver is False  # Do not deliver duplicate payload!
        assert ack == 0          # But re-send ACK 0 so sender unblocks!
        assert rec.expected_seq == 1

    def test_out_of_order_future_packet_does_not_ack_zero(self):
        rec = StopAndWaitReceiver()
        # Packet 2 arrives when expecting 0: must NOT acknowledge packet 0!
        deliver, ack = rec.process_packet(2)
        assert deliver is False
        assert ack is None
        assert rec.expected_seq == 0

    def test_out_of_order_packet_with_previous_packets(self):
        rec = StopAndWaitReceiver()
        rec.process_packet(0)
        rec.process_packet(1)
        assert rec.expected_seq == 2

        # Packet 4 arrives when expecting 2: re-ack packet 1 (last valid received)
        deliver, ack = rec.process_packet(4)
        assert deliver is False
        assert ack == 1
        assert rec.expected_seq == 2


# ===================================================================
# Timestamp Conversion Tests
# ===================================================================

class TestTimestampConversions:
    def test_ns_to_s(self):
        assert ns_to_s(1_000_000_000) == pytest.approx(1.0)
        assert ns_to_s(500_000_000) == pytest.approx(0.5)
        assert ns_to_s(0) == pytest.approx(0.0)

    def test_s_to_ns(self):
        assert s_to_ns(1.0) == 1_000_000_000
        assert s_to_ns(0.5) == 500_000_000
        assert s_to_ns(0.0) == 0
