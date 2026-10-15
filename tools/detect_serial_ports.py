#!/usr/bin/env python3
"""Inventory serial devices and Raspberry Pi UART configuration without opening ports."""

from __future__ import annotations

import argparse
import glob
import os
import platform
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from rescue_core.common.diagnostics import ensure_output_directory, read_text_if_present, run_command, write_json


DEVICE_PATTERNS = ("/dev/serial*", "/dev/ttyAMA*", "/dev/ttyACM*", "/dev/ttyUSB*")
BOOT_CONFIGS = ("/boot/firmware/config.txt", "/boot/config.txt")
CMDLINES = ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt", "/proc/cmdline")


def _device(path: str) -> dict[str, Any]:
    candidate = Path(path)
    try:
        target = str(candidate.resolve(strict=True)) if candidate.is_symlink() else None
        stat = candidate.stat()
        return {
            "path": path,
            "exists": True,
            "symlink_target": target,
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
            "mode": oct(stat.st_mode & 0o777),
        }
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return {"path": path, "exists": candidate.exists(), "error": str(exc)}


def _relevant_lines(text: str, terms: tuple[str, ...]) -> list[str]:
    return [line.strip() for line in text.splitlines() if any(term in line.lower() for term in terms)]


def collect_serial_info(configured_connection: str | None = None) -> dict[str, Any]:
    paths = sorted({path for pattern in DEVICE_PATTERNS for path in glob.glob(pattern)})
    boot: dict[str, Any] = {}
    for path in BOOT_CONFIGS:
        text = read_text_if_present(path)
        if text is not None:
            boot[path] = _relevant_lines(text, ("enable_uart", "dtoverlay", "console"))
    cmdlines: dict[str, Any] = {}
    consoles: list[str] = []
    for path in CMDLINES:
        text = read_text_if_present(path)
        if text is not None:
            cmdlines[path] = text.strip()
            consoles.extend(token for token in text.split() if token.startswith("console="))
    result: dict[str, Any] = {
        "status": "collected",
        "host": {"system": platform.system(), "kernel": platform.release()},
        "uname": run_command(["uname", "-a"]),
        "os_release": read_text_if_present("/etc/os-release"),
        "identity": run_command(["id"]),
        "groups": run_command(["groups"]),
        "devices": [_device(path) for path in paths],
        "serial_symlink_listings": {
            path: run_command(["ls", "-l", path])
            for path in ("/dev/serial0", "/dev/serial1")
            if Path(path).exists()
        },
        "boot_configuration": boot,
        "kernel_cmdlines": cmdlines,
        "serial_consoles": consoles,
        "configured_connection": configured_connection,
    }
    if configured_connection:
        if configured_connection.startswith(("udp:", "udpin:", "udpout:", "tcp:")):
            result["configured_connection_status"] = "network_endpoint_not_filesystem"
        else:
            result["configured_connection_status"] = (
                "exists" if Path(configured_connection).exists() else "missing"
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", help="Configured device to check (never opened)")
    parser.add_argument("--output-dir", help="Existing diagnostic run directory")
    args = parser.parse_args()
    output = ensure_output_directory(args.output_dir)
    destination = output / "serial.json"
    write_json(destination, collect_serial_info(args.connection))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
