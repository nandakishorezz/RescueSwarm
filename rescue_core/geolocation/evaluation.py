"""Ground-truth localization metrics; source accuracy remains part of evidence."""

from __future__ import annotations

import math

import numpy as np


def geolocation_metrics(errors: list[tuple[str, float, float]]) -> dict[str, object]:
    """Evaluate (target_id, east_error_m, north_error_m) records."""
    if not errors:
        return {"status": "NOT_MEASURED", "reason": "GROUND TRUTH REQUIRED", "targets": []}
    radial = np.asarray([math.hypot(east, north) for _, east, north in errors])
    return {
        "status": "MEASURED", "target_count": len(errors),
        "targets": [
            {"target_id": target_id, "east_error_m": east, "north_error_m": north,
             "error_m": math.hypot(east, north)}
            for target_id, east, north in errors
        ],
        "mean_error_m": float(np.mean(radial)), "median_error_m": float(np.median(radial)),
        "rmse_m": float(np.sqrt(np.mean(np.square(radial)))),
        "p50_m": float(np.percentile(radial, 50)), "p90_m": float(np.percentile(radial, 90)),
        "p95_m": float(np.percentile(radial, 95)), "maximum_m": float(np.max(radial)),
        "mean_east_error_m": float(np.mean([east for _, east, _ in errors])),
        "mean_north_error_m": float(np.mean([north for _, _, north in errors])),
    }

