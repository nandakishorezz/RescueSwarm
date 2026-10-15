"""Portable CPU, memory, temperature, and Raspberry Pi throttling sampling."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil


@dataclass(slots=True)
class ResourceSample:
    timestamp_monotonic_ns: int
    cpu_percent: float
    process_rss_bytes: int
    system_memory_percent: float
    temperature_c: float | None
    throttling: str | None

    def as_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


class ResourceMonitor:
    def __init__(self):
        self.process = psutil.Process()
        self.process.cpu_percent(None)

    def sample(self) -> ResourceSample:
        return ResourceSample(
            timestamp_monotonic_ns=time.monotonic_ns(),
            cpu_percent=self.process.cpu_percent(None),
            process_rss_bytes=self.process.memory_info().rss,
            system_memory_percent=psutil.virtual_memory().percent,
            temperature_c=cpu_temperature(),
            throttling=throttling_status(),
        )


def cpu_temperature() -> float | None:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        value = float(path.read_text(encoding="utf-8").strip())
        return value / 1000 if value > 200 else value
    except (OSError, ValueError):
        return None


def throttling_status() -> str | None:
    try:
        completed = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True,
            timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip()
    return output or None


def throttling_detected(samples: list[ResourceSample]) -> bool | None:
    known = [sample.throttling for sample in samples if sample.throttling]
    if not known:
        return None
    values: list[int] = []
    for item in known:
        try:
            values.append(int(item.split("=", 1)[-1], 16))
        except ValueError:
            continue
    return any(value != 0 for value in values) if values else None


def system_snapshot() -> dict[str, Any]:
    model_path = Path("/proc/device-tree/model")
    try:
        model = model_path.read_text(encoding="utf-8").strip("\x00\n ")
    except OSError:
        model = None
    packages: dict[str, str | None] = {}
    for name in ("numpy", "Pillow", "psutil", "onnxruntime", "picamera2", "ncnn"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "raspberry_pi": bool(model and "Raspberry Pi" in model),
        "pi_model": model or "NOT_RASPBERRY_PI",
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "ram_total_bytes": psutil.virtual_memory().total,
        "user": os.getenv("USER") or os.getenv("USERNAME"),
        "packages": packages,
        "camera_stack": {
            "rpicam": _version_command("rpicam-hello", "--version"),
            "libcamera_legacy_name": _version_command("libcamera-hello", "--version"),
        },
    }


def _version_command(executable: str, argument: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, argument], capture_output=True, text=True, timeout=3, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or result.stderr).strip() or None
