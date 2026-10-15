#!/usr/bin/env python3
"""Evaluate physical geolocation estimates against provenance-rich ground truth."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.common.diagnostics import write_json
from rescue_core.hardware.metrics import physical_geolocation_errors, uncertainty_calibration_verdict
from rescue_core.hardware.models import GroundTruthTarget


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("estimates", type=Path, help="NDJSON estimates from a physical session")
    parser.add_argument("ground_truth", type=Path, help="GroundTruthTarget JSON")
    parser.add_argument("--test", choices=("A", "B", "C"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--intended-coverage-percent", type=float, default=95)
    args = parser.parse_args()
    truth = GroundTruthTarget.model_validate_json(args.ground_truth.read_text(encoding="utf-8"))
    if truth.expected_accuracy_m is None or not truth.notes.strip():
        parser.error("Physical ground truth requires expected_accuracy_m and non-empty notes")
    estimates = [json.loads(line) for line in args.estimates.read_text(encoding="utf-8").splitlines() if line.strip()]
    metrics = physical_geolocation_errors(estimates, truth)
    result = {
        "test": args.test,
        "scope": {"A": "relative_camera_geometry", "B": "camera_pixhawk_attitude_timing", "C": "complete_m8n_geolocation"}[args.test],
        "metrics": metrics,
        "uncertainty_calibration": uncertainty_calibration_verdict(metrics, args.intended_coverage_percent),
    }
    write_json(args.output, result)
    print(args.output)
    return 0 if metrics["status"] == "MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
