#!/usr/bin/env python3
"""Enumerate Picamera2 cameras and sensor modes without changing configuration."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.system_metrics import system_snapshot


def list_modes(camera_index: int = 0) -> dict[str, object]:
    system = system_snapshot()
    if not system["raspberry_pi"]:
        return {"status": "NOT_TESTED", "reason": "Host is not a Raspberry Pi", "system": system, "cameras": []}
    try:
        from picamera2 import Picamera2
    except ImportError:
        return {"status": "FAILED", "reason": "Picamera2 is not installed", "system": system, "cameras": []}
    try:
        cameras = Picamera2.global_camera_info()
        if not cameras:
            return {"status": "FAILED", "reason": "Picamera2 enumerated no cameras", "system": system, "cameras": []}
        camera = Picamera2(camera_index)
        try:
            modes = [_serializable(mode) for mode in camera.sensor_modes]
            properties = _serializable(camera.camera_properties)
        finally:
            camera.close()
        return {
            "status": "PASS", "reason": "Camera and sensor modes enumerated",
            "system": system, "camera_index": camera_index,
            "cameras": _serializable(cameras), "sensor_modes": modes,
            "camera_properties": properties, "platform": platform.platform(),
        }
    except Exception as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}", "system": system, "cameras": []}


def _serializable(value):
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def validate_profile_against_modes(
    modes_result: dict[str, object], width: int, height: int, fps: float,
) -> tuple[bool, str]:
    modes = modes_result.get("sensor_modes", [])
    for mode in modes if isinstance(modes, list) else []:
        if not isinstance(mode, dict):
            continue
        size = mode.get("size")
        mode_fps = mode.get("fps")
        if isinstance(size, list) and size == [width, height]:
            if mode_fps is None or float(mode_fps) + 1e-6 >= fps:
                return True, f"Selected {width}x{height}@{fps:g} is present in discovered sensor modes"
            return False, f"Selected FPS {fps:g} exceeds discovered {mode_fps} FPS for {width}x{height}"
    return False, f"Selected resolution {width}x{height} is absent from discovered sensor modes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("camera_modes.json"))
    args = parser.parse_args()
    result = list_modes(args.camera_index)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if result["status"] in {"PASS", "NOT_TESTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
