"""Hardware measurement statistics with explicit truth semantics."""

from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any, Iterable

import numpy as np
from pyproj import Geod

from .models import GroundTruthTarget

GEOD = Geod(ellps="WGS84")


def _percentile(values: list[float], percentile: float) -> float | None:
    return None if not values else float(np.percentile(np.asarray(values), percentile))


def gps_stationary_statistics(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Measure stationary repeatability; never label it absolute accuracy."""

    valid = [s for s in samples if s.get("latitude_deg") is not None and s.get("longitude_deg") is not None]
    if not valid:
        return {"status": "NOT_TESTED", "reason": "No valid GPS coordinates", "absolute_accuracy": "NOT_MEASURED"}
    lat0 = median(float(s["latitude_deg"]) for s in valid)
    lon0 = median(float(s["longitude_deg"]) for s in valid)
    offsets: list[tuple[float, float]] = []
    for sample in valid:
        azimuth, _, distance = GEOD.inv(lon0, lat0, float(sample["longitude_deg"]), float(sample["latitude_deg"]))
        radians = math.radians(azimuth)
        offsets.append((distance * math.sin(radians), distance * math.cos(radians)))
    radial = [math.hypot(east, north) for east, north in offsets]
    first, last = valid[0], valid[-1]
    _, _, drift = GEOD.inv(float(first["longitude_deg"]), float(first["latitude_deg"]), float(last["longitude_deg"]), float(last["latitude_deg"]))
    return {
        "status": "MEASURED",
        "metric": "stationary_repeatability_not_absolute_accuracy",
        "sample_count": len(valid),
        "median_position": {"latitude_deg": lat0, "longitude_deg": lon0},
        "east_scatter_std_m": pstdev([point[0] for point in offsets]) if len(offsets) > 1 else 0.0,
        "north_scatter_std_m": pstdev([point[1] for point in offsets]) if len(offsets) > 1 else 0.0,
        "radial_deviation_m": {"p50": _percentile(radial, 50), "p95": _percentile(radial, 95), "maximum": max(radial)},
        "first_to_last_drift_m": drift,
        "absolute_accuracy": "NOT_MEASURED",
    }


def stream_rate_statistics(timestamps_ns: Iterable[int]) -> dict[str, Any]:
    times = sorted(int(value) for value in timestamps_ns)
    if len(times) < 2:
        return {"status": "NOT_TESTED", "sample_count": len(times)}
    intervals_ms = [(b - a) / 1_000_000 for a, b in zip(times, times[1:]) if b > a]
    if not intervals_ms:
        return {"status": "FAILED", "reason": "Non-increasing timestamps"}
    duration_s = (times[-1] - times[0]) / 1_000_000_000
    return {
        "status": "MEASURED",
        "sample_count": len(times),
        "observed_hz": (len(times) - 1) / duration_s,
        "interval_ms": {"p50": _percentile(intervals_ms, 50), "p95": _percentile(intervals_ms, 95), "maximum": max(intervals_ms)},
        "jitter_std_ms": pstdev(intervals_ms) if len(intervals_ms) > 1 else 0.0,
    }


def attitude_static_statistics(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    valid = [s for s in samples if all(s.get(axis) is not None for axis in ("roll_deg", "pitch_deg", "yaw_deg"))]
    if not valid:
        return {"status": "NOT_TESTED", "reason": "No complete attitude samples"}
    result: dict[str, Any] = {"status": "MEASURED", "sample_count": len(valid)}
    for axis in ("roll_deg", "pitch_deg", "yaw_deg"):
        values = [float(s[axis]) for s in valid]
        result[axis] = {"mean": mean(values), "std": pstdev(values) if len(values) > 1 else 0.0, "p95_variation": _percentile([abs(value - median(values)) for value in values], 95)}
    return result


def frame_pose_sync_statistics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    if not rows:
        return {"status": "NOT_TESTED", "reason": "No frame/pose synchronization records"}
    bracketed = [row for row in rows if row.get("pose_before_ns") is not None and row.get("pose_after_ns") is not None]
    gaps = [(int(row["pose_after_ns"]) - int(row["pose_before_ns"])) / 1_000_000 for row in bracketed]
    return {
        "status": "MEASURED",
        "frames": len(rows),
        "bracketed_frames": len(bracketed),
        "bracketing_success_percent": 100.0 * len(bracketed) / len(rows),
        "gap_ms": {"p50": _percentile(gaps, 50), "p95": _percentile(gaps, 95), "maximum": max(gaps) if gaps else None},
        "rejected_frames": len(rows) - len(bracketed),
    }


def physical_geolocation_errors(estimates: Iterable[dict[str, Any]], truth: GroundTruthTarget) -> dict[str, Any]:
    rows = list(estimates)
    errors: list[tuple[float, float, float, float | None]] = []
    for row in rows:
        if truth.east_m is not None and row.get("east_m") is not None:
            east = float(row["east_m"]) - truth.east_m
            north = float(row["north_m"]) - float(truth.north_m)
        elif truth.true_latitude is not None and row.get("latitude") is not None:
            azimuth, _, radial = GEOD.inv(truth.true_longitude, truth.true_latitude, float(row["longitude"]), float(row["latitude"]))
            east, north = radial * math.sin(math.radians(azimuth)), radial * math.cos(math.radians(azimuth))
        else:
            continue
        uncertainty = float(row["position_uncertainty_m"]) if row.get("position_uncertainty_m") is not None else None
        errors.append((east, north, math.hypot(east, north), uncertainty))
    if not errors:
        return {"status": "NOT_TESTED", "reason": "No estimates compatible with ground truth"}
    radial = [item[2] for item in errors]
    inside = [item[2] <= item[3] for item in errors if item[3] is not None]
    return {
        "status": "MEASURED",
        "ground_truth": truth.model_dump(mode="json"),
        "sample_count": len(errors),
        "east_error_m": {"mean": mean(item[0] for item in errors), "median": median(item[0] for item in errors)},
        "north_error_m": {"mean": mean(item[1] for item in errors), "median": median(item[1] for item in errors)},
        "radial_error_m": {"p50": _percentile(radial, 50), "p90": _percentile(radial, 90), "p95": _percentile(radial, 95), "maximum": max(radial)},
        "uncertainty_coverage_percent": None if not inside else 100.0 * sum(inside) / len(inside),
        "absolute_accuracy_measured": truth.source.value in {"SURVEYED", "SURVEYED_GNSS", "RTK"},
    }


def uncertainty_calibration_verdict(metrics: dict[str, Any], intended_coverage_percent: float = 95) -> dict[str, Any]:
    observed = metrics.get("uncertainty_coverage_percent")
    if observed is None:
        return {"status": "UNKNOWN", "reason": "Predicted uncertainty was not present on measured estimates"}
    tolerance = 5.0
    if observed < intended_coverage_percent - tolerance:
        label = "OVERCONFIDENT"
    elif observed > min(100.0, intended_coverage_percent + tolerance):
        label = "CONSERVATIVE"
    else:
        label = "WELL_CALIBRATED"
    return {
        "status": label,
        "observed_coverage_percent": observed,
        "intended_coverage_percent": intended_coverage_percent,
        "sample_count": metrics.get("sample_count"),
        "note": "Interpretation is meaningful only for an adequate, representative physical sample set",
    }
