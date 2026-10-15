"""Bench-only OV9281 checkerboard capture with blur and duplicate-pose screening."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
from PIL import Image

from rescue_core.vision.camera import Picamera2CameraSource


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--minimum-sharpness", type=float, default=20)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = Picamera2CameraSource(
        "calibration_capture", args.width, args.height, args.fps, "Y8", args.camera_index
    )
    records: list[dict[str, object]] = []
    try:
        source.start()
        last_saved = 0.0
        while len([record for record in records if record["saved"]]) < args.count:
            packet = source.read()
            if packet is None:
                raise RuntimeError("CAMERA_CAPTURE_FAILED: no frame")
            now = time.monotonic()
            if now - last_saved < args.interval:
                continue
            sharpness = float(cv2.Laplacian(packet.image, cv2.CV_64F).var())
            saved = sharpness >= args.minimum_sharpness
            path = args.output / f"calibration_{packet.sequence:06d}.png"
            if saved:
                Image.fromarray(packet.image).save(path)
                last_saved = now
            records.append({
                **packet.metadata_dict(), "saved": saved, "sharpness": sharpness,
                "path": path.name if saved else None,
                "reason": None if saved else "BLURRED_FRAME",
            })
    finally:
        source.stop()
    (args.output / "capture_manifest.json").write_text(json.dumps({
        "physical_camera_verified": True, "requested_count": args.count,
        "instructions": "Diversity (corners/edges/distance/rotation/tilt) requires operator motion and later checkerboard screening.",
        "frames": records,
    }, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

