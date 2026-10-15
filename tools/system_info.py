#!/usr/bin/env python3
"""Collect non-invasive host and Raspberry Pi platform information."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from rescue_core.common.diagnostics import ensure_output_directory, read_text_if_present, run_command, write_json


def _first_line(path: str) -> str | None:
    text = read_text_if_present(path)
    return text.strip("\x00\n ") if text else None


def _memory() -> dict[str, Any]:
    text = read_text_if_present("/proc/meminfo")
    if not text:
        return {"total_bytes": None, "available_bytes": None}
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, raw = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            values[key] = int(raw.strip().split()[0]) * 1024
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
    }


def _temperature() -> float | None:
    raw = _first_line("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        value = float(raw)
        return value / 1000.0 if value > 200 else value
    except ValueError:
        return None


def _packages() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in ("pymavlink", "PyYAML", "pydantic", "pytest"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def collect_system_info() -> dict[str, Any]:
    model = _first_line("/proc/device-tree/model")
    is_pi = bool(model and "Raspberry Pi" in model)
    disk = shutil.disk_usage(Path.cwd())
    network = run_command(["ip", "-json", "address"], timeout=5)
    if network["status"] == "not_available":
        network = {"hostname": socket.gethostname(), "addresses": socket.getaddrinfo(socket.gethostname(), None)}
    return {
        "status": "collected",
        "platform": {
            "raspberry_pi": is_pi,
            "model": model or "Not identified as Raspberry Pi",
            "os": platform.platform(),
            "os_release": platform.freedesktop_os_release() if sys.platform.startswith("linux") else {},
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
        },
        "memory": _memory(),
        "cpu_temperature_c": _temperature(),
        "disk": {"path": str(Path.cwd()), "total_bytes": disk.total, "free_bytes": disk.free},
        "network_interfaces": network,
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        "packages": _packages(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="Existing diagnostic run directory")
    args = parser.parse_args()
    output = ensure_output_directory(args.output_dir)
    destination = output / "system.json"
    write_json(destination, collect_system_info())
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
