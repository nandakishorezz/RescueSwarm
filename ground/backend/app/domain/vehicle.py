"""Normalized vehicle state, independent of telemetry source."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LinkState(StrEnum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


class VehicleState(BaseModel):
    vehicle_id: str
    name: str
    connected: bool = False
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    relative_altitude_m: float = 0
    absolute_altitude_m: float | None = None
    roll_deg: float = 0
    pitch_deg: float = 0
    yaw_deg: float = 0
    ground_speed_m_s: float = Field(default=0, ge=0)
    vertical_speed_m_s: float | None = None
    velocity_ned_m_s: tuple[float, float, float] | None = None
    quaternion_body_to_ned: tuple[float, float, float, float] | None = None
    attitude_source: str = "EULER"
    battery_percent: float = Field(default=100, ge=0, le=100)
    battery_voltage: float = Field(default=16.8, ge=0)
    gps_fix_type: int = Field(default=3, ge=0)
    satellites: int = Field(default=12, ge=0)
    flight_mode: str = "SIMULATED"
    armed: bool = False
    link_quality: float = Field(default=100, ge=0, le=100)
    current_task: str = "IDLE"
    last_update: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence: int = Field(default=0, ge=0)
    simulated: bool = True
