#!/usr/bin/env python3
"""Create ignored local camera configuration only from measured Pi evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.hardware.profiles import write_local_vision_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("camera_modes", type=Path)
    parser.add_argument("benchmark_summary", type=Path)
    parser.add_argument("--mode-index", type=int, required=True)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--pixel-format", default="Y8")
    parser.add_argument("--evidence-run", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "config" / "local_vision.yaml")
    parser.add_argument("--base-config", type=Path, default=ROOT / "config" / "vision.yaml")
    args = parser.parse_args()
    modes = json.loads(args.camera_modes.read_text(encoding="utf-8"))
    benchmark = json.loads(args.benchmark_summary.read_text(encoding="utf-8"))
    if modes.get("status") != "PASS" or benchmark.get("status") != "PASS":
        raise ValueError("Camera enumeration and benchmark must both PASS before generating a local profile")
    selected = modes.get("sensor_modes", [])[args.mode_index]
    size = selected.get("size")
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError("Selected sensor mode has no usable size evidence")
    achieved = benchmark.get("capture_fps")
    path = write_local_vision_profile(args.output, {
        "camera_index": args.camera_index, "sensor_mode": f"sensor_modes[{args.mode_index}]",
        "sensor_mode_evidence": selected, "width": int(size[0]), "height": int(size[1]),
        "pixel_format": args.pixel_format, "achieved_fps": float(achieved),
        "evidence_run": args.evidence_run,
    }, args.base_config)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
