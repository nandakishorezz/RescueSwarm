"""Create a hashed manual/approximate fixed camera mount entry; never claims measurement automatically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from rescue_core.geolocation.calibration import CameraExtrinsics, ExtrinsicsMethod, ExtrinsicsQuality


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extrinsics-id", required=True)
    parser.add_argument("--roll-deg", type=float, required=True)
    parser.add_argument("--pitch-deg", type=float, required=True)
    parser.add_argument("--yaw-deg", type=float, required=True)
    parser.add_argument("--translation-forward-m", type=float, required=True)
    parser.add_argument("--translation-right-m", type=float, required=True)
    parser.add_argument("--translation-down-m", type=float, required=True)
    parser.add_argument("--method", choices=[value.value for value in ExtrinsicsMethod], required=True)
    parser.add_argument("--quality", choices=[value.value for value in ExtrinsicsQuality], required=True)
    parser.add_argument("--rotation-uncertainty-deg", type=float)
    parser.add_argument("--translation-uncertainty-m", type=float)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--notes")
    args = parser.parse_args()
    extrinsics = CameraExtrinsics.from_euler_degrees(
        extrinsics_id=args.extrinsics_id, roll_deg=args.roll_deg, pitch_deg=args.pitch_deg,
        yaw_deg=args.yaw_deg,
        translation_body_m=(args.translation_forward_m, args.translation_right_m, args.translation_down_m),
        method=ExtrinsicsMethod(args.method), quality=ExtrinsicsQuality(args.quality),
        rotation_uncertainty_deg=args.rotation_uncertainty_deg,
        translation_uncertainty_m=args.translation_uncertainty_m, notes=args.notes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(extrinsics.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "extrinsics_id": extrinsics.extrinsics_id,
                      "quality": extrinsics.quality, "sha256": extrinsics.sha256}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

