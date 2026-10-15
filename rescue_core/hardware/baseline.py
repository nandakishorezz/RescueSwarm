"""Immutable Stage 6 read-only hardware baseline collection."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rescue_core.common.diagnostics import read_text_if_present, run_command, write_json
from tools.camera_probe import collect_camera_info
from tools.detect_serial_ports import collect_serial_info
from tools.system_info import collect_system_info

from .models import EvidenceStatus
from .power import parse_throttled_output


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _command_version(command: list[str]) -> dict[str, Any]:
    return run_command(command, timeout=10)


def collect_power_health() -> dict[str, Any]:
    command = run_command(["vcgencmd", "get_throttled"], timeout=5)
    parsed = parse_throttled_output(command.get("stdout"))
    temperature = run_command(["vcgencmd", "measure_temp"], timeout=5)
    return {**parsed.model_dump(mode="json"), "command": command, "temperature_command": temperature}


def collect_runtime_environment() -> dict[str, Any]:
    system = collect_system_info()
    system["stage6_packages"] = {
        "Picamera2": _version("picamera2"),
        "libcamera": _version("libcamera"),
        "opencv": _version("opencv-python-headless") or _version("opencv-python"),
        "ncnn": _version("ncnn"),
        "onnxruntime": _version("onnxruntime"),
    }
    system["camera_stack_versions"] = {
        "rpicam_apps": _command_version(["rpicam-hello", "--version"]),
        "libcamera": _command_version(["libcamera-hello", "--version"]),
    }
    return system


def collect_network() -> dict[str, Any]:
    return {
        "status": "collected",
        "hostname": socket.gethostname(),
        "interfaces": run_command(["ip", "-json", "address"], timeout=5),
        "routes": run_command(["ip", "route"], timeout=5),
        "external_network_required": False,
    }


def _not_tested(reason: str) -> dict[str, Any]:
    return {"status": EvidenceStatus.NOT_TESTED, "reason": reason}


class HardwareBaselineCollector:
    """Collect evidence without changing boot, network, camera, or autopilot state."""

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def collect_host(self) -> dict[str, Any]:
        system = collect_runtime_environment()
        write_json(self.output_dir / "system.json", system)
        write_json(self.output_dir / "power.json", collect_power_health())
        write_json(self.output_dir / "serial_devices.json", collect_serial_info())
        write_json(self.output_dir / "network.json", collect_network())
        boot_sources: list[str] = []
        for source in ("/boot/firmware/config.txt", "/boot/config.txt", "/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
            content = read_text_if_present(source)
            if content is not None:
                boot_sources.append(f"# SOURCE: {source}\n{content.rstrip()}\n")
        (self.output_dir / "boot_config.txt").write_text(
            "\n".join(boot_sources) if boot_sources else "NOT_TESTED: Raspberry Pi boot files unavailable on this host\n",
            encoding="utf-8",
        )
        camera, dmesg = collect_camera_info()
        if not system.get("platform", {}).get("raspberry_pi"):
            camera = {
                **camera,
                "probe_classification": camera.get("status"),
                "status": EvidenceStatus.NOT_TESTED,
                "reason": "Physical camera was not tested because this host is not a Raspberry Pi",
            }
        write_json(self.output_dir / "camera_probe.json", camera)
        (self.output_dir / "camera_dmesg.txt").write_text(dmesg + ("\n" if dmesg else ""), encoding="utf-8")
        return system

    def record_mavlink_absent(self, reason: str) -> None:
        for name in ("mavlink_probe.json", "autopilot_version.json", "gps.json", "compass.json", "ekf.json"):
            write_json(self.output_dir / name, _not_tested(reason))
        (self.output_dir / "parameters.param").write_text(f"# NOT_TESTED: {reason}\n", encoding="utf-8")
        write_json(self.output_dir / "parameters.sha256.json", _not_tested("No physical parameter snapshot to hash"))

    def ingest_mavlink_result(self, result: dict[str, Any]) -> None:
        write_json(self.output_dir / "mavlink_probe.json", result)
        write_json(self.output_dir / "autopilot_version.json", result.get("firmware") or _not_tested("AUTOPILOT_VERSION unavailable"))
        telemetry = result.get("telemetry") or {}
        gps = telemetry.get("gps")
        write_json(self.output_dir / "gps.json", {"status": "VERIFIED", **gps} if gps else _not_tested("GPS_RAW_INT unavailable"))
        highlighted = ((result.get("parameters") or {}).get("highlighted") or {})
        compass_parameters = {key: value for key, value in highlighted.items() if key.startswith("COMPASS")}
        write_json(self.output_dir / "compass.json", {
            "status": "VERIFIED" if telemetry.get("heading_deg") is not None else "UNKNOWN",
            "heading_deg": telemetry.get("heading_deg"),
            "parameter_evidence": compass_parameters,
            "reason": "Heading telemetry observed" if telemetry.get("heading_deg") is not None else "Compass identity/health not available in passive telemetry",
        })
        ekf = telemetry.get("ekf_status")
        write_json(self.output_dir / "ekf.json", {"status": "VERIFIED", **ekf} if ekf else _not_tested("EKF_STATUS_REPORT unavailable"))
        parameter_file = self.output_dir / "parameters.param"
        if parameter_file.exists():
            digest = hashlib.sha256(parameter_file.read_bytes()).hexdigest()
            timestamp = self.output_dir.name.split("_", 2)[:2]
            stamped_name = f"stage6_parameters_{'_'.join(timestamp)}.param"
            stamped = self.output_dir / stamped_name
            shutil.copyfile(parameter_file, stamped)
            write_json(self.output_dir / "parameters.sha256.json", {
                "algorithm": "sha256", "sha256": digest,
                "files": [parameter_file.name, stamped.name],
            })

    def manifest(self) -> dict[str, Any]:
        files = sorted(path.name for path in self.output_dir.iterdir() if path.is_file())
        model = read_text_if_present("/proc/device-tree/model") or ""
        manifest = {
            "schema_version": 1,
            "stage": 6,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "host": platform.platform(),
            "physical_hardware_accessed": "Raspberry Pi 5" in model,
            "flight_commands_sent": False,
            "configuration_modified": False,
            "files": files,
        }
        write_json(self.output_dir / "run_manifest.json", manifest)
        return manifest


def copy_parameter_snapshot(source: str | Path, destination: str | Path) -> None:
    shutil.copyfile(Path(source), Path(destination))
