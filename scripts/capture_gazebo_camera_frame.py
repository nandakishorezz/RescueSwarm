"""Capture one local Gazebo image as Phase-1 observer evidence."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
from pathlib import Path

from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--topic", default="/rescueswarm_reference_quad/ov9281_reference_simulation/image")
    args = parser.parse_args()
    result = subprocess.run(
        ["wsl.exe", "-d", "Ubuntu", "--", "gz", "topic", "--json-output", "-e", "-n", "1", "-t", args.topic],
        check=True, capture_output=True, text=True, timeout=30,
    )
    payload = json.loads(result.stdout)
    width, height = int(payload["width"]), int(payload["height"])
    raw = base64.b64decode(payload["data"])
    if len(raw) != width * height:
        raise RuntimeError(f"expected {width * height} mono bytes, received {len(raw)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("L", (width, height), raw).save(args.output)
    metadata = {
        "classification": "SIMULATION_OBSERVER_CAMERA_EVIDENCE",
        "topic": args.topic,
        "width": width,
        "height": height,
        "pixel_format": payload.get("pixel_format_type", payload.get("pixel_format")),
        "sim_timestamp": payload.get("header", {}).get("stamp"),
        "phase2_detector_connected": False,
    }
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
