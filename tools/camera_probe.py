#!/usr/bin/env python3
"""Collect read-only Raspberry Pi CSI/OV9281 camera evidence."""

from __future__ import annotations

import argparse
import glob
import platform
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from rescue_core.common.diagnostics import ensure_output_directory, read_text_if_present, run_command, write_json


CAMERA_COMMANDS = (
    ["rpicam-hello", "--list-cameras"],
    ["rpicam-still", "--list-cameras"],
    ["libcamera-hello", "--list-cameras"],
    ["libcamera-still", "--list-cameras"],
    ["vcgencmd", "get_camera"],
    ["dtoverlay", "-l"],
    ["i2cdetect", "-l"],
    ["media-ctl", "-p"],
)


def classify_camera(commands: list[dict[str, Any]], config_text: str, dmesg: str) -> tuple[str, str]:
    combined = "\n".join(
        f"{item.get('stdout', '')}\n{item.get('stderr', '')}" for item in commands
    ).lower()
    kernel = dmesg.lower()
    configured = "ov9281" in config_text.lower() or "camera_auto_detect=1" in config_text.lower()
    sensor_named = "ov9281" in combined or "ov9282" in combined
    no_cameras = "no cameras available" in combined or "available cameras\n-----------------\n" in combined
    if sensor_named and not no_cameras:
        return "DETECTED", "OV9281/OV9282 sensor was enumerated by a camera utility"
    driver_named = "ov9281" in kernel or "ov9282" in kernel
    failure_terms = ("failed", "error", "not found", "no such", "probe failed", "-5")
    if driver_named and any(term in kernel for term in failure_terms):
        return "DRIVER_LOADED_BUT_SENSOR_NOT_RESPONDING", "Kernel named the sensor and logged a probe/error condition"
    if configured:
        return "CONFIGURATION_PRESENT_BUT_NOT_DETECTED", "Camera configuration exists but no sensor enumeration was observed"
    if no_cameras or (not glob.glob("/dev/video*") and not glob.glob("/dev/media*")):
        return "NO_CAMERA_DEVICE", "No camera enumeration or media/video device evidence was found"
    return "UNKNOWN", "Some camera-related evidence exists but it does not identify the OV9281"


def collect_camera_info() -> tuple[dict[str, Any], str]:
    commands = [run_command(command, timeout=20) for command in CAMERA_COMMANDS]
    configs: dict[str, str] = {}
    for path in ("/boot/firmware/config.txt", "/boot/config.txt"):
        text = read_text_if_present(path)
        if text is not None:
            configs[path] = "\n".join(
                line for line in text.splitlines()
                if any(term in line.lower() for term in ("camera_auto_detect", "ov9281", "dtoverlay"))
            )
    dmesg_result = run_command(["dmesg"], timeout=15)
    dmesg_raw = f"{dmesg_result.get('stdout', '')}\n{dmesg_result.get('stderr', '')}"
    terms = ("ov9281", "ov9282", "unicam", "csi", "camera", "i2c", "regulator")
    filtered = "\n".join(line for line in dmesg_raw.splitlines() if any(t in line.lower() for t in terms))
    config_text = "\n".join(configs.values())
    state, reason = classify_camera(commands, config_text, filtered)
    result = {
        "status": state,
        "reason": reason,
        "kernel": platform.release(),
        "devices": {
            "video": sorted(glob.glob("/dev/video*")),
            "media": sorted(glob.glob("/dev/media*")),
        },
        "boot_configuration": configs,
        "commands": commands,
        "dmesg_access": dmesg_result["status"],
    }
    return result, filtered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="Existing diagnostic run directory")
    args = parser.parse_args()
    output = ensure_output_directory(args.output_dir)
    result, dmesg = collect_camera_info()
    write_json(output / "camera.json", result)
    (output / "camera_dmesg.txt").write_text(dmesg + ("\n" if dmesg else ""), encoding="utf-8")
    print(output / "camera.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
