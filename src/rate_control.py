

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger("rate_controller")


class RateControlMode(str, Enum):
    """Operating mode for the rate controller."""

    ADAPTIVE = "adaptive"
    FIXED = "fixed"


@dataclass
class RateControllerConfig:
    """Configuration parameters for AdaptiveRateController.

    Units:
        Rates: packets per second (pkt/s)
        Intervals: seconds (s)
        Thresholds: RTT and jitter in milliseconds (ms); loss in fraction [0.0, 1.0]
    """

    mode: RateControlMode = RateControlMode.ADAPTIVE
    initial_rate: float = 100.0  # pkt/s (corresponds to 10ms interval)
    min_rate: float = 10.0  # pkt/s (minimum allowable transmission rate)
    max_rate: float = 500.0  # pkt/s (maximum allowable transmission rate)
    increase_step: float = 10.0  # pkt/s (additive increase step)
    decrease_factor: float = 0.7  # Multiplicative decrease factor (0.0 < factor < 1.0)
    rtt_threshold_ms: float = 80.0  # ms (RTT threshold triggering backoff)
    jitter_threshold_ms: float = 20.0  # ms (Jitter threshold triggering backoff)
    loss_threshold: float = 0.03  # 3% packet loss threshold
    smoothing_factor: float = 0.125  # EWMA smoothing factor for RTT and jitter
    min_adjustment_interval_s: float = 0.05  # Minimum time between rate updates to prevent oscillation

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Validate configuration settings."""
        if isinstance(self.mode, str):
            try:
                self.mode = RateControlMode(self.mode.lower())
            except ValueError:
                raise ValueError(
                    f"Invalid mode {self.mode!r}. Must be 'adaptive' or 'fixed'."
                )

        if not (0 < self.min_rate <= self.max_rate):
            raise ValueError(
                f"Invalid rate limits: min_rate ({self.min_rate}) must be positive "
                f"and <= max_rate ({self.max_rate})."
            )

        if not (self.min_rate <= self.initial_rate <= self.max_rate):
            raise ValueError(
                f"initial_rate ({self.initial_rate}) must be within [{self.min_rate}, {self.max_rate}]."
            )

        if self.increase_step <= 0.0:
            raise ValueError(
                f"increase_step must be positive, got {self.increase_step}."
            )

        if not (0.0 < self.decrease_factor < 1.0):
            raise ValueError(
                f"decrease_factor must be in (0.0, 1.0), got {self.decrease_factor}."
            )

        if self.rtt_threshold_ms <= 0.0:
            raise ValueError("rtt_threshold_ms must be positive.")

        if self.jitter_threshold_ms < 0.0:
            raise ValueError("jitter_threshold_ms must be non-negative.")

        if not (0.0 <= self.loss_threshold <= 1.0):
            raise ValueError("loss_threshold must be in [0.0, 1.0].")

        if not (0.0 < self.smoothing_factor <= 1.0):
            raise ValueError("smoothing_factor must be in (0.0, 1.0].")

        if self.min_adjustment_interval_s < 0.0:
            raise ValueError("min_adjustment_interval_s must be non-negative.")


@dataclass
class RateAdjustmentRecord:
    """Record of a transmission rate evaluation and adjustment."""

    timestamp: float
    mode: str
    previous_rate: float
    new_rate: float
    measured_rtt_ms: Optional[float]
    smoothed_rtt_ms: Optional[float]
    measured_jitter_ms: Optional[float]
    smoothed_jitter_ms: Optional[float]
    loss_ratio: Optional[float]
    reason: str


class AdaptiveRateController:
    """Rule-based AIMD Transmission-Rate Controller.

    Adjusts packet sending interval based on observed RTT, jitter, and packet loss.
    """

    def __init__(self, config: Optional[RateControllerConfig] = None):
        self.config = config or RateControllerConfig()
        self.current_rate: float = float(self.config.initial_rate)

        # Smoothed metrics (EWMA)
        self.smoothed_rtt_ms: Optional[float] = None
        self.smoothed_jitter_ms: Optional[float] = None
        self.recent_loss_ratio: Optional[float] = None

        self.last_adjustment_time: float = 0.0
        self.adjustment_history: List[RateAdjustmentRecord] = []

    @property
    def mode(self) -> RateControlMode:
        return self.config.mode

    @mode.setter
    def mode(self, val: RateControlMode):
        self.config.mode = val

    def get_rate(self) -> float:
        """Return the current transmission rate in packets per second (pkt/s)."""
        return self.current_rate

    def get_inter_packet_interval(self) -> float:
        """Return the inter-packet sleep interval in seconds."""
        if self.current_rate <= 0:
            return 1.0 / self.config.min_rate
        return 1.0 / self.current_rate

    def update_metrics(
        self,
        rtt_ms: Optional[float] = None,
        jitter_ms: Optional[float] = None,
        loss_ratio: Optional[float] = None,
        now: Optional[float] = None,
    ) -> RateAdjustmentRecord:
        """Update controller with latest network feedback and adjust transmission rate.

        Parameters:
            rtt_ms: Round-Trip Time measurement in milliseconds.
            jitter_ms: Delay variation measurement in milliseconds.
            loss_ratio: Recent packet loss fraction in [0.0, 1.0].
            now: Monotonic or wall-clock timestamp (seconds).
        """
        current_time = time.time() if now is None else now
        previous_rate = self.current_rate

        # Fixed rate mode: never adapt rate
        if self.config.mode == RateControlMode.FIXED:
            record = RateAdjustmentRecord(
                timestamp=current_time,
                mode="fixed",
                previous_rate=previous_rate,
                new_rate=previous_rate,
                measured_rtt_ms=rtt_ms,
                smoothed_rtt_ms=self.smoothed_rtt_ms,
                measured_jitter_ms=jitter_ms,
                smoothed_jitter_ms=self.smoothed_jitter_ms,
                loss_ratio=loss_ratio,
                reason="fixed_rate_mode",
            )
            self.adjustment_history.append(record)
            return record

        # Filter and smooth valid metrics
        alpha = self.config.smoothing_factor
        if rtt_ms is not None and not math.isnan(rtt_ms) and rtt_ms >= 0.0:
            if self.smoothed_rtt_ms is None:
                self.smoothed_rtt_ms = rtt_ms
            else:
                self.smoothed_rtt_ms = (1.0 - alpha) * self.smoothed_rtt_ms + alpha * rtt_ms

        if jitter_ms is not None and not math.isnan(jitter_ms) and jitter_ms >= 0.0:
            if self.smoothed_jitter_ms is None:
                self.smoothed_jitter_ms = jitter_ms
            else:
                self.smoothed_jitter_ms = (1.0 - alpha) * self.smoothed_jitter_ms + alpha * jitter_ms

        if loss_ratio is not None and not math.isnan(loss_ratio) and 0.0 <= loss_ratio <= 1.0:
            self.recent_loss_ratio = loss_ratio

        # Condition C: Insufficient or missing metrics (none of the metrics are available)
        if (
            self.smoothed_rtt_ms is None
            and self.recent_loss_ratio is None
            and self.smoothed_jitter_ms is None
        ):
            reason = "insufficient_measurements_hold"
            record = RateAdjustmentRecord(
                timestamp=current_time,
                mode="adaptive",
                previous_rate=previous_rate,
                new_rate=previous_rate,
                measured_rtt_ms=rtt_ms,
                smoothed_rtt_ms=self.smoothed_rtt_ms,
                measured_jitter_ms=jitter_ms,
                smoothed_jitter_ms=self.smoothed_jitter_ms,
                loss_ratio=loss_ratio,
                reason=reason,
            )
            self.adjustment_history.append(record)
            return record

        # Check adjustment interval (hysteresis safeguard against oscillation)
        if (current_time - self.last_adjustment_time) < self.config.min_adjustment_interval_s:
            reason = "rate_change_damped_by_min_interval"
            record = RateAdjustmentRecord(
                timestamp=current_time,
                mode="adaptive",
                previous_rate=previous_rate,
                new_rate=previous_rate,
                measured_rtt_ms=rtt_ms,
                smoothed_rtt_ms=self.smoothed_rtt_ms,
                measured_jitter_ms=jitter_ms,
                smoothed_jitter_ms=self.smoothed_jitter_ms,
                loss_ratio=loss_ratio,
                reason=reason,
            )
            self.adjustment_history.append(record)
            return record

        # Condition A: Network Congestion or Deterioration
        congestion_reasons = []
        if (
            self.recent_loss_ratio is not None
            and self.recent_loss_ratio > self.config.loss_threshold
        ):
            congestion_reasons.append(
                f"loss {self.recent_loss_ratio*100:.1f}% > {self.config.loss_threshold*100:.1f}%"
            )

        if (
            self.smoothed_rtt_ms is not None
            and self.smoothed_rtt_ms > self.config.rtt_threshold_ms
        ):
            congestion_reasons.append(
                f"rtt {self.smoothed_rtt_ms:.1f}ms > {self.config.rtt_threshold_ms:.1f}ms"
            )

        if (
            self.smoothed_jitter_ms is not None
            and self.smoothed_jitter_ms > self.config.jitter_threshold_ms
        ):
            congestion_reasons.append(
                f"jitter {self.smoothed_jitter_ms:.1f}ms > {self.config.jitter_threshold_ms:.1f}ms"
            )

        if congestion_reasons:
            # Multiplicative Decrease
            raw_new_rate = self.current_rate * self.config.decrease_factor
            self.current_rate = max(self.config.min_rate, raw_new_rate)
            self.last_adjustment_time = current_time
            reason = f"congestion_decrease: {', '.join(congestion_reasons)}"
            logger.info("AIMD Backoff: %.1f -> %.1f pkt/s (%s)", previous_rate, self.current_rate, reason)

        else:
            # Condition B: Stable Network Conditions
            # Require all available metrics to be comfortably below thresholds
            loss_ok = (
                self.recent_loss_ratio is None
                or self.recent_loss_ratio <= (self.config.loss_threshold * 0.5)
            )
            rtt_ok = (
                self.smoothed_rtt_ms is None
                or self.smoothed_rtt_ms <= (self.config.rtt_threshold_ms * 0.8)
            )
            jitter_ok = (
                self.smoothed_jitter_ms is None
                or self.smoothed_jitter_ms <= (self.config.jitter_threshold_ms * 0.8)
            )

            if loss_ok and rtt_ok and jitter_ok:
                # Additive Increase
                raw_new_rate = self.current_rate + self.config.increase_step
                self.current_rate = min(self.config.max_rate, raw_new_rate)
                self.last_adjustment_time = current_time
                reason = "stable_conditions_additive_increase"
                logger.debug("AIMD Increase: %.1f -> %.1f pkt/s", previous_rate, self.current_rate)
            else:
                # In the deadband/hold region
                reason = "stable_hold"

        # Condition D: Rate Limits (Sanity bounds)
        self.current_rate = min(
            self.config.max_rate, max(self.config.min_rate, self.current_rate)
        )
        if math.isnan(self.current_rate) or self.current_rate <= 0:
            self.current_rate = self.config.min_rate

        record = RateAdjustmentRecord(
            timestamp=current_time,
            mode="adaptive",
            previous_rate=previous_rate,
            new_rate=self.current_rate,
            measured_rtt_ms=rtt_ms,
            smoothed_rtt_ms=self.smoothed_rtt_ms,
            measured_jitter_ms=jitter_ms,
            smoothed_jitter_ms=self.smoothed_jitter_ms,
            loss_ratio=loss_ratio,
            reason=reason,
        )
        self.adjustment_history.append(record)
        return record
