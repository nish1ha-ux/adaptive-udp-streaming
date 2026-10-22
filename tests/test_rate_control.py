"""Unit tests for AdaptiveRateController."""

import pytest

from src.rate_control import (
    AdaptiveRateController,
    RateControlMode,
    RateControllerConfig,
)


def test_controller_config_validation():
    """Verify validation of configuration parameters."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        min_rate=20.0,
        max_rate=300.0,
        increase_step=5.0,
        decrease_factor=0.8,
        rtt_threshold_ms=50.0,
        jitter_threshold_ms=10.0,
        loss_threshold=0.05,
    )
    cfg.validate()

    # min_rate > max_rate
    with pytest.raises(ValueError):
        RateControllerConfig(min_rate=200.0, max_rate=100.0)

    # initial_rate out of range
    with pytest.raises(ValueError):
        RateControllerConfig(initial_rate=500.0, max_rate=300.0)

    # decrease_factor >= 1.0 or <= 0.0
    with pytest.raises(ValueError):
        RateControllerConfig(decrease_factor=1.5)
    with pytest.raises(ValueError):
        RateControllerConfig(decrease_factor=-0.1)

    # Invalid mode
    with pytest.raises(ValueError):
        RateControllerConfig(mode="unsupported")


def test_initial_rate_and_interval_conversion():
    """Verify initial rate and inter-packet interval conversion."""
    cfg = RateControllerConfig(initial_rate=100.0)
    controller = AdaptiveRateController(cfg)

    assert controller.get_rate() == 100.0
    # 100 pkt/s -> 1/100 s = 0.010 s = 10 ms interval
    assert controller.get_inter_packet_interval() == pytest.approx(0.010, rel=1e-5)


def test_rate_reduction_on_loss():
    """Verify multiplicative decrease when packet loss exceeds threshold."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        decrease_factor=0.7,
        loss_threshold=0.03,
        min_adjustment_interval_s=0.0,
    )
    controller = AdaptiveRateController(cfg)

    record = controller.update_metrics(loss_ratio=0.08, now=1.0)
    assert controller.get_rate() == pytest.approx(70.0, rel=1e-5)
    assert "loss" in record.reason.lower()


def test_rate_reduction_on_rtt_deterioration():
    """Verify multiplicative decrease when smoothed RTT exceeds threshold."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        decrease_factor=0.8,
        rtt_threshold_ms=80.0,
        smoothing_factor=1.0,  # immediate update
        min_adjustment_interval_s=0.0,
    )
    controller = AdaptiveRateController(cfg)

    record = controller.update_metrics(rtt_ms=120.0, now=1.0)
    assert controller.get_rate() == pytest.approx(80.0, rel=1e-5)
    assert "rtt" in record.reason.lower()


def test_rate_reduction_on_high_jitter():
    """Verify multiplicative decrease when smoothed jitter exceeds threshold."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        decrease_factor=0.75,
        jitter_threshold_ms=15.0,
        smoothing_factor=1.0,
        min_adjustment_interval_s=0.0,
    )
    controller = AdaptiveRateController(cfg)

    record = controller.update_metrics(jitter_ms=30.0, now=1.0)
    assert controller.get_rate() == pytest.approx(75.0, rel=1e-5)
    assert "jitter" in record.reason.lower()


def test_additive_increase_under_stable_conditions():
    """Verify additive increase when conditions are comfortably stable."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        increase_step=15.0,
        rtt_threshold_ms=100.0,
        jitter_threshold_ms=20.0,
        loss_threshold=0.05,
        min_adjustment_interval_s=0.0,
    )
    controller = AdaptiveRateController(cfg)

    # Stable conditions: loss=0%, rtt=20ms, jitter=2ms
    record = controller.update_metrics(rtt_ms=20.0, jitter_ms=2.0, loss_ratio=0.0, now=1.0)
    assert controller.get_rate() == pytest.approx(115.0, rel=1e-5)
    assert "stable_conditions_additive_increase" in record.reason


def test_enforcement_of_min_and_max_rates():
    """Verify controller strictly clamps rate between min_rate and max_rate."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        min_rate=20.0,
        max_rate=120.0,
        increase_step=30.0,
        decrease_factor=0.1,
        min_adjustment_interval_s=0.0,
    )
    controller = AdaptiveRateController(cfg)

    # Additive increase towards max
    controller.update_metrics(rtt_ms=10.0, jitter_ms=1.0, loss_ratio=0.0, now=1.0)
    assert controller.get_rate() == 120.0  # capped at max_rate

    # Heavy congestion triggering backoff
    controller.update_metrics(loss_ratio=0.20, now=2.0)
    # 120 * 0.1 = 12, but min_rate is 20.0
    assert controller.get_rate() == 20.0


def test_oscillation_protection_via_min_interval():
    """Verify rate adjustments are throttled within the minimum interval."""
    cfg = RateControllerConfig(
        initial_rate=100.0,
        increase_step=10.0,
        min_adjustment_interval_s=1.0,
    )
    controller = AdaptiveRateController(cfg)

    rec1 = controller.update_metrics(rtt_ms=10.0, jitter_ms=1.0, loss_ratio=0.0, now=10.0)
    assert controller.get_rate() == 110.0

    # Immediate second update (0.1s later) should be held
    rec2 = controller.update_metrics(rtt_ms=10.0, jitter_ms=1.0, loss_ratio=0.0, now=10.1)
    assert controller.get_rate() == 110.0
    assert "damped" in rec2.reason

    # After interval has passed (1.1s later)
    rec3 = controller.update_metrics(rtt_ms=10.0, jitter_ms=1.0, loss_ratio=0.0, now=11.2)
    assert controller.get_rate() == 120.0


def test_fixed_rate_mode_does_not_adapt():
    """Verify fixed-rate mode ignores metric fluctuations."""
    cfg = RateControllerConfig(
        mode=RateControlMode.FIXED,
        initial_rate=50.0,
        increase_step=20.0,
        decrease_factor=0.5,
    )
    controller = AdaptiveRateController(cfg)

    # Massive loss
    rec1 = controller.update_metrics(loss_ratio=0.50, rtt_ms=300.0, now=1.0)
    assert controller.get_rate() == 50.0
    assert rec1.reason == "fixed_rate_mode"

    # Perfect conditions
    rec2 = controller.update_metrics(loss_ratio=0.0, rtt_ms=5.0, now=2.0)
    assert controller.get_rate() == 50.0


def test_handling_invalid_or_missing_metrics():
    """Verify safe fallback when metrics are missing, NaN, or negative."""
    cfg = RateControllerConfig(initial_rate=80.0, min_adjustment_interval_s=0.0)
    controller = AdaptiveRateController(cfg)

    # All None
    rec = controller.update_metrics(rtt_ms=None, jitter_ms=None, loss_ratio=None, now=1.0)
    assert controller.get_rate() == 80.0
    assert "insufficient" in rec.reason

    # NaN and negative values
    rec2 = controller.update_metrics(rtt_ms=float("nan"), jitter_ms=-10.0, loss_ratio=2.5, now=2.0)
    assert controller.get_rate() == 80.0
