"""Compare local fused targets with provenance-bearing ground truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from rescue_core.geolocation.evaluation import geolocation_metrics
from rescue_core.geolocation.models import GroundTruthTarget


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--origin-latitude", type=float, required=True)
    parser.add_argument("--origin-longitude", type=float, required=True)
    parser.add_argument("--origin-altitude-m", type=float, default=0)
    parser.add_argument("--output", type=Path, default=Path("geolocation_metrics.json"))
    args = parser.parse_args()
    estimates = {item["target_id"]: item for item in json.loads(args.targets.read_text(encoding="utf-8"))}
    truths = [GroundTruthTarget.model_validate(item) for item in json.loads(args.ground_truth.read_text(encoding="utf-8"))["targets"]]
    frame = LocalCoordinateFrame(GeoPoint(
        latitude=args.origin_latitude, longitude=args.origin_longitude, altitude_m=args.origin_altitude_m
    ))
    errors = []
    provenance = []
    for truth in truths:
        estimate = estimates.get(truth.target_id)
        if not estimate:
            continue
        true_enu = frame.to_enu(GeoPoint(latitude=truth.true_latitude, longitude=truth.true_longitude, altitude_m=args.origin_altitude_m))
        errors.append((truth.target_id, estimate["east_m"] - true_enu.east_m, estimate["north_m"] - true_enu.north_m))
        provenance.append({"target_id": truth.target_id, "source": truth.source, "expected_accuracy_m": truth.expected_accuracy_m})
    metrics = {**geolocation_metrics(errors), "ground_truth_provenance": provenance}
    args.output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(args.output)
    return 0 if errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

