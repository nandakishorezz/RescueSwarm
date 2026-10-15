"""Evaluate a hashed camera calibration on held-out local checkerboard images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rescue_core.geolocation.calibration import CalibrationRegistry
from rescue_core.geolocation.calibration_workflow import validate_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_id")
    parser.add_argument("images", type=Path)
    parser.add_argument("--registry", type=Path, default=Path("calibration/cameras"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    calibration = CalibrationRegistry(args.registry).load_intrinsics(args.calibration_id)
    output = args.output or (args.registry / args.calibration_id / "held_out_validation")
    images = sorted(path for path in args.images.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
    result = validate_calibration(calibration, images, output / "previews")
    path = output / "validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(path)
    return 0 if result["status"] == "MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

