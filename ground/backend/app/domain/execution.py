"""Execution records shared by simulator, SITL, and future real vehicle sources."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .mission import GeoPoint


class ExecutionSource(StrEnum):
    SIMULATED = "SIMULATED"
    SITL = "SITL"
    REAL = "REAL"


class ExecutionState(StrEnum):
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    SITL_CONNECTING = "SITL_CONNECTING"
    SITL_READY = "SITL_READY"
    MISSION_UPLOADING = "MISSION_UPLOADING"
    MISSION_VERIFIED = "MISSION_VERIFIED"
    ARMING = "ARMING"
    TAKING_OFF = "TAKING_OFF"
    SEARCHING = "SEARCHING"
    DELIVERING = "DELIVERING"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class MissionExecution(BaseModel):
    mission_id: str
    source: ExecutionSource
    state: ExecutionState
    mission_fingerprint: str | None = None
    autopilot_version: str | None = None
    ardupilot_git_commit: str | None = None
    connection: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    current_mission_item: int = 0
    total_mission_items: int = 0
    waypoints_reached: list[int] = Field(default_factory=list)
    actual_trail: list[GeoPoint] = Field(default_factory=list)
    actual_path_length_m: float = Field(default=0, ge=0)
    actual_coverage_percent: float = Field(default=0, ge=0, le=100)
    missed_area_m2: float | None = Field(default=None, ge=0)
    overlap_area_m2: float | None = Field(default=None, ge=0)
    mean_deviation_m: float | None = Field(default=None, ge=0)
    p95_deviation_m: float | None = Field(default=None, ge=0)
    max_deviation_m: float | None = Field(default=None, ge=0)
    rtl_completed: bool = False
    landing_detected: bool = False
    disarm_detected: bool = False
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
