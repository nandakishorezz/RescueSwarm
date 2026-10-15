"""Validated project configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class VehicleConfig(BaseModel):
    id: str
    fleet_size: int = Field(ge=1)


class AutopilotConfig(BaseModel):
    type: Literal["ardupilot"]
    flight_controller: str
    connection: str
    baud: int = Field(gt=0)
    heartbeat_timeout_seconds: float = Field(gt=0)
    collection_seconds: float = Field(ge=10, le=30)


class GpsConfig(BaseModel):
    type: str
    precision_gnss: bool


class EdgeConfig(BaseModel):
    type: str
    ram_gb: int = Field(gt=0)
    ai_accelerator: str


class CameraConfig(BaseModel):
    type: str
    global_shutter: bool
    status: str


class GroundConfig(BaseModel):
    type: str
    ram_gb: int = Field(gt=0)


class NetworkConfig(BaseModel):
    external_internet_required: bool


class PrototypeConfig(BaseModel):
    vehicle: VehicleConfig
    autopilot: AutopilotConfig
    gps: GpsConfig
    edge: EdgeConfig
    camera: CameraConfig
    ground: GroundConfig
    rangefinder: dict[str, object]
    payload: dict[str, object]
    network: NetworkConfig


def load_config(path: str | Path) -> PrototypeConfig:
    """Load and validate a prototype YAML configuration file."""

    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file does not exist: {config_path}")
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration root must be a mapping: {config_path}")
    return PrototypeConfig.model_validate(raw)
