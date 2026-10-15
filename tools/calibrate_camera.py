"""Calibrate a local checkerboard dataset as pinhole-Brown or fisheye."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rescue_core.geolocation.calibration import CalibrationRegistry, DistortionModel
from rescue_core.geolocation.calibration_workflow import calibrate_checkerboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--sensor", default="OV9281")
    parser.add_argument("--sensor-mode", required=True)
    parser.add_argument("--columns", type=int, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--square-size-mm", type=float, required=True, help="Physically measured printed square size")
    parser.add_argument("--model", choices=[value.value for value in DistortionModel], required=True)
    parser.add_argument("--registry", type=Path, default=Path("calibration/cameras"))
    args = parser.parse_args()
    paths = sorted(path for path in args.images.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
    calibration, validation = calibrate_checkerboard(
        calibration_id=args.calibration_id, sensor=args.sensor, sensor_mode=args.sensor_mode,
        image_paths=paths, pattern_dimensions=(args.columns, args.rows),
        square_size_m=args.square_size_mm / 1000, distortion_model=DistortionModel(args.model),
    )
    directory = CalibrationRegistry(args.registry).save(calibration, None, validation)
    print(json.dumps({"directory": str(directory), "verdict": calibration.verdict,
                      "rms_px": calibration.rms_reprojection_error, "p95_px": calibration.p95_reprojection_error}, indent=2))
    return 0 if calibration.verdict.value != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())

