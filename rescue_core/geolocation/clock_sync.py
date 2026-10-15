"""Robust MAVLink TIMESYNC offset estimation without changing either clock."""

from __future__ import annotations

import statistics
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class ClockDomain(StrEnum):
    GAZEBO_SIM = "GAZEBO_SIM"
    HOST_MONOTONIC = "HOST_MONOTONIC"
    BRIDGE_MONOTONIC = "BRIDGE_MONOTONIC"
    ARDUPILOT_BOOT = "ARDUPILOT_BOOT"
    ARDUPILOT_SIM_SOURCE = "ARDUPILOT_SIM_SOURCE"
    ARDUPILOT_TIMESYNC = "ARDUPILOT_TIMESYNC"
    WALL_CLOCK = "WALL_CLOCK"


@dataclass(frozen=True)
class Timestamp:
    value_ns: int
    domain: ClockDomain

    def __post_init__(self) -> None:
        if type(self.value_ns) is not int or self.value_ns < 0:
            raise ValueError("Timestamp must be nonnegative integer nanoseconds")
        if not isinstance(self.domain, ClockDomain):
            raise TypeError("Timestamp requires an explicit ClockDomain")

    def __sub__(self, other: "Timestamp") -> int:
        if self.domain != other.domain:
            raise TypeError("Clock-domain subtraction requires an explicit mapping")
        return self.value_ns - other.value_ns


class MappingQuality(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    CONVERGING = "CONVERGING"
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


class AffineClockMapping:
    """Bounded, centered robust affine fit; uncertainty is in destination time.

    This measures fit residuals, not unknowable one-way transport bias. Callers
    must supply a defensible delay bound or keep the combined mapping invalid.
    """

    def __init__(self, source: ClockDomain, destination: ClockDomain, *,
                 max_samples: int = 256, window_ns: int = 5_000_000_000,
                 min_samples: int = 12, min_span_ns: int = 500_000_000,
                 max_error_ns: int = 16_700_000, max_age_ns: int = 1_000_000_000):
        if max_samples < min_samples or min_samples < 3 or min_span_ns <= 0 or window_ns <= 0:
            raise ValueError("Invalid affine clock window")
        self.source, self.destination = source, destination
        self.samples: deque[tuple[int, int, int, int]] = deque(maxlen=max_samples)
        self.window_ns, self.min_samples, self.min_span_ns = window_ns, min_samples, min_span_ns
        self.max_error_ns, self.max_age_ns = max_error_ns, max_age_ns
        self.epoch = 0
        self.version = 0
        self.reset_reason: str | None = None
        self._lock = threading.RLock()

    def invalidate(self, reason: str) -> None:
        with self._lock:
            self.samples.clear()
            self.epoch += 1
            self.reset_reason = reason

    def observe(self, source: Timestamp, destination: Timestamp, *,
                host_received_ns: int, uncertainty_ns: int = 0) -> None:
        if source.domain != self.source or destination.domain != self.destination:
            raise TypeError("Clock mapping sample domain mismatch")
        if uncertainty_ns < 0:
            raise ValueError("Negative timestamp uncertainty")
        with self._lock:
            if self.samples and source.value_ns < self.samples[-1][0]:
                self.invalidate("SOURCE_CLOCK_RESET")
            if self.samples and source.value_ns == self.samples[-1][0]:
                return
            self.samples.append((source.value_ns, destination.value_ns, host_received_ns, uncertainty_ns))
            while self.samples and host_received_ns - self.samples[0][2] > self.window_ns:
                self.samples.popleft()
            self.version += 1

    def snapshot(self, now_host_ns: int) -> dict[str, object]:
        with self._lock:
            samples = list(self.samples)
            result: dict[str, object] = {
                "source": self.source.value, "destination": self.destination.value,
                "version": self.version, "epoch": self.epoch, "sample_count": len(samples),
                "quality": MappingQuality.UNINITIALIZED.value, "reset_reason": self.reset_reason,
            }
        if not samples:
            return result
        age = now_host_ns - samples[-1][2]
        result.update(age_host_ns=age, quality=MappingQuality.CONVERGING.value)
        if age < 0 or age > self.max_age_ns:
            result["quality"] = MappingQuality.INVALID.value
            return result
        if len(samples) < self.min_samples or samples[-1][0] - samples[0][0] < self.min_span_ns:
            return result
        x0, y0 = samples[0][:2]
        x = np.array([row[0] - x0 for row in samples], dtype=np.float64)
        y = np.array([row[1] - y0 for row in samples], dtype=np.float64)
        design = np.column_stack((x / 1e9, np.ones(len(x))))
        weights = np.array([1 / max(row[3] / 1e6, 1.0) for row in samples])
        coefficients = np.linalg.lstsq(design * weights[:, None], y * weights, rcond=None)[0]
        residual = y - design @ coefficients
        mad = float(np.median(np.abs(residual - np.median(residual))))
        keep = np.abs(residual - np.median(residual)) <= max(1_000_000, 6 * mad)
        if keep.sum() >= self.min_samples:
            coefficients = np.linalg.lstsq(design[keep] * weights[keep, None], y[keep] * weights[keep], rcond=None)[0]
        slope, intercept = float(coefficients[0] / 1e9), float(coefficients[1])
        # Score ALL samples, including rejected fit points, to expose drift.
        errors = np.abs(y - design @ coefficients)
        p95 = float(np.percentile(errors, 95))
        bound = p95 + max(row[3] for row in samples)
        if self.source == ClockDomain.HOST_MONOTONIC and self.destination == ClockDomain.ARDUPILOT_TIMESYNC:
            # TIMESYNC interval widths originate in host time. Never understate
            # them if simulated AP time advances faster than host time.
            bound = p95 + max(row[3] for row in samples) * max(1.0, abs(slope))
        quality = MappingQuality.VALID if math.isfinite(slope) and slope > 0 and bound <= self.max_error_ns else MappingQuality.DEGRADED
        result.update(
            quality=quality.value, slope=slope, source_anchor_ns=x0,
            destination_anchor_ns=y0, intercept_at_anchor_ns=intercept,
            offset_ns=y0 + intercept - slope * x0,
            residual_p50_ns=float(np.percentile(errors, 50)), residual_p95_ns=p95,
            maximum_residual_ns=float(errors.max()), uncertainty_ns=bound,
            fit_outliers=int((~keep).sum()),
        )
        return result

    def map(self, stamp: Timestamp, now_host_ns: int) -> tuple[Timestamp, dict[str, object]]:
        if stamp.domain != self.source:
            raise TypeError("Clock conversion source domain mismatch")
        snapshot = self.snapshot(now_host_ns)
        if snapshot["quality"] != MappingQuality.VALID.value:
            raise ValueError(f"CLOCK_MAPPING_INVALID: {snapshot['quality']}")
        value = int(snapshot["destination_anchor_ns"]) + round(
            float(snapshot["slope"]) * (stamp.value_ns - int(snapshot["source_anchor_ns"]))
            + float(snapshot["intercept_at_anchor_ns"])
        )
        return Timestamp(value, self.destination), snapshot


class ClockSyncQuality(StrEnum):
    UNSYNCED = "UNSYNCED"
    WARMING_UP = "WARMING_UP"
    SYNCED = "SYNCED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ClockSyncSample:
    local_send_ns: int
    local_receive_ns: int
    remote_time_ns: int
    round_trip_ns: int
    offset_ns: int


class ClockSyncEstimator:
    """Map host monotonic nanoseconds to autopilot time using TIMESYNC replies.

    For a reply with the original local transmit timestamp ``ts1`` and remote
    timestamp ``tc1``, the NTP-style estimate is
    ``offset = tc1 - (local_send + local_receive)/2``.
    """

    def __init__(
        self, minimum_samples: int = 5, maximum_samples: int = 100,
        max_rtt_ms: float = 30, degraded_rtt_p95_ms: float = 20,
        *, affine: bool = False,
    ):
        if minimum_samples < 2 or maximum_samples < minimum_samples:
            raise ValueError("Clock sync sample limits are invalid")
        self.minimum_samples = minimum_samples
        self.max_rtt_ns = int(max_rtt_ms * 1e6)
        self.degraded_rtt_p95_ns = int(degraded_rtt_p95_ms * 1e6)
        self.samples: deque[ClockSyncSample] = deque(maxlen=maximum_samples)
        self.rejected_samples = 0
        self.accepted_samples = 0
        self.affine = AffineClockMapping(
            ClockDomain.HOST_MONOTONIC, ClockDomain.ARDUPILOT_TIMESYNC,
            min_samples=max(3, minimum_samples), max_samples=max(12, maximum_samples),
            window_ns=2_000_000_000,
        ) if affine else None

    def observe_response(self, local_send_ns: int, remote_time_ns: int, local_receive_ns: int) -> bool:
        round_trip = local_receive_ns - local_send_ns
        if remote_time_ns <= 0 or round_trip < 0 or round_trip > self.max_rtt_ns:
            self.rejected_samples += 1
            return False
        offset = int(remote_time_ns - (local_send_ns + local_receive_ns) / 2)
        if self.affine and self.samples and remote_time_ns < self.samples[-1].remote_time_ns:
            self.samples.clear()
            self.affine.invalidate("ARDUPILOT_CLOCK_RESET")
        if self.affine is None and len(self.samples) >= self.minimum_samples:
            offsets = [sample.offset_ns for sample in self.samples]
            median = statistics.median(offsets)
            deviations = [abs(value - median) for value in offsets]
            mad = statistics.median(deviations)
            robust_gate = max(1_000_000, 6 * mad)
            if abs(offset - median) > robust_gate:
                self.rejected_samples += 1
                return False
        self.samples.append(ClockSyncSample(local_send_ns, local_receive_ns, remote_time_ns, round_trip, offset))
        self.accepted_samples += 1
        if self.affine:
            self.affine.observe(
                Timestamp((local_send_ns + local_receive_ns) // 2, ClockDomain.HOST_MONOTONIC),
                Timestamp(remote_time_ns, ClockDomain.ARDUPILOT_TIMESYNC),
                host_received_ns=local_receive_ns,
                # Host half RTT is converted conservatively in qualification.
                uncertainty_ns=round_trip // 2,
            )
        return True

    @property
    def offset_ns(self) -> int | None:
        if not self.samples:
            return None
        # Lowest-delay samples are least affected by asymmetric link latency.
        ordered = sorted(self.samples, key=lambda sample: sample.round_trip_ns)
        best = ordered[: max(self.minimum_samples, len(ordered) // 2)]
        return int(statistics.median(sample.offset_ns for sample in best))

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

    @property
    def quality(self) -> ClockSyncQuality:
        if self.affine:
            quality = self.affine.snapshot(time.monotonic_ns())["quality"]
            return {
                "VALID": ClockSyncQuality.SYNCED, "DEGRADED": ClockSyncQuality.DEGRADED,
                "INVALID": ClockSyncQuality.FAILED, "CONVERGING": ClockSyncQuality.WARMING_UP,
                "UNINITIALIZED": ClockSyncQuality.UNSYNCED,
            }[quality]
        if not self.samples:
            return ClockSyncQuality.UNSYNCED
        if len(self.samples) < self.minimum_samples:
            return ClockSyncQuality.WARMING_UP
        rtt_p95 = self._percentile([sample.round_trip_ns for sample in self.samples], 0.95)
        return ClockSyncQuality.DEGRADED if rtt_p95 and rtt_p95 > self.degraded_rtt_p95_ns else ClockSyncQuality.SYNCED

    def host_to_autopilot_ns(self, host_monotonic_ns: int) -> int:
        if self.affine:
            mapped, _ = self.affine.map(Timestamp(host_monotonic_ns, ClockDomain.HOST_MONOTONIC), time.monotonic_ns())
            return mapped.value_ns
        offset = self.offset_ns
        if offset is None:
            raise ValueError("CLOCK_UNSYNCED")
        return host_monotonic_ns + offset

    def diagnostics(self) -> dict[str, object]:
        rtts = [sample.round_trip_ns for sample in self.samples]
        return {
            "offset_ns": self.offset_ns,
            "sample_count": len(self.samples),
            "accepted_samples": self.accepted_samples,
            "rejected_samples": self.rejected_samples,
            "rtt_median_ms": statistics.median(rtts) / 1e6 if rtts else None,
            "rtt_p95_ms": self._percentile(rtts, 0.95) / 1e6 if rtts else None,
            "sync_quality": self.quality,
            "mode": "AFFINE" if self.affine else "OFFSET",
            "affine_mapping": self.affine.snapshot(time.monotonic_ns()) if self.affine else None,
            "offset_variance_ns2": statistics.pvariance([sample.offset_ns for sample in self.samples]) if self.samples else None,
        }
