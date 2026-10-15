"""Generated, evidence-linked local hardware profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class LocalAutopilotProfile(BaseModel):
    connection: str
    baud: int = Field(gt=0)
    evidence_run: str
    read_only: bool = True


class LocalHardwareProfile(BaseModel):
    generated: bool = True
    autopilot: LocalAutopilotProfile
    physical_connection: str = "UNKNOWN"
    pixhawk_port: str = "UNKNOWN"
    notes: list[str] = Field(default_factory=list)


def load_local_hardware_profile(path: str | Path) -> LocalHardwareProfile:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return LocalHardwareProfile.model_validate(raw)


def write_local_hardware_profile(path: str | Path, profile: LocalHardwareProfile) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return destination


def write_local_vision_profile(
    path: str | Path, evidence: dict[str, Any], base_config: str | Path | None = None,
) -> Path:
    required = ("camera_index", "sensor_mode", "width", "height", "pixel_format", "achieved_fps")
    missing = [key for key in required if evidence.get(key) is None]
    if missing:
        raise ValueError("Camera evidence is incomplete: " + ", ".join(missing))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if base_config:
        payload = yaml.safe_load(Path(base_config).read_text(encoding="utf-8"))
        camera = payload["camera"]
        camera["source"] = "picamera2"
        camera["profile"] = "detection"
        camera["camera_index"] = evidence["camera_index"]
        camera["physical_evidence"] = {
            "run": evidence.get("evidence_run"),
            "sensor_mode": evidence["sensor_mode"],
            "sensor_mode_evidence": evidence.get("sensor_mode_evidence"),
        }
        camera["profiles"]["detection"] = {
            "width": evidence["width"], "height": evidence["height"],
            "fps": evidence["achieved_fps"], "pixel_format": evidence["pixel_format"],
            "verification": "EVIDENCE_BACKED_LOCAL",
        }
    else:
        payload = {"generated": True, "evidence_run": evidence.get("evidence_run"), "camera": evidence}
    destination.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return destination
