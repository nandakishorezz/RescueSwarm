"""Phase-1 fleet configuration and Phase-2 hand-off contracts.

The contracts do not authorize or execute a delivery. In particular an
observation is never an assignment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DroneRole(StrEnum):
    SEARCHER = "SEARCHER"
    SERVICER = "SERVICER"


class DroneInstance(BaseModel):
    vehicle_id: str = Field(pattern=r"^drone_\d{2}$")
    role: DroneRole
    system_id: int = Field(ge=1, le=255)
    instance_id: int = Field(ge=0)
    spawn_north_m: float = Field(ge=0)
    spawn_heading_deg: float = Field(default=90, ge=0, lt=360)


class FleetConfiguration(BaseModel):
    mission_id: str
    drones: list[DroneInstance] = Field(min_length=2, max_length=4)
    spawn_spacing_m: float = Field(default=2, ge=1.5, le=20)
    disable_servicer_camera: bool = Field(default=False)

    @model_validator(mode="after")
    def unique_and_supported(self) -> "FleetConfiguration":
        if len({item.vehicle_id for item in self.drones}) != len(self.drones):
            raise ValueError("Fleet vehicle IDs must be unique")
        if len({item.system_id for item in self.drones}) != len(self.drones):
            raise ValueError("MAVLink system IDs must be unique")
        for index, drone in enumerate(self.drones):
            if (drone.vehicle_id != f"drone_{index + 1:02d}" or drone.instance_id != index
                    or drone.system_id != index + 1):
                raise ValueError("Current generated-world instances require ordered drone IDs and SYSIDs")
            if abs(drone.spawn_north_m - index * self.spawn_spacing_m) > 1e-6 or drone.spawn_heading_deg != 90:
                raise ValueError("Spawn configuration must match generated Gazebo and SITL offsets")
        return self


def default_configuration(mission_id: str) -> FleetConfiguration:
    return FleetConfiguration(mission_id=mission_id, drones=[
        DroneInstance(vehicle_id="drone_01", role=DroneRole.SEARCHER, system_id=1, instance_id=0, spawn_north_m=0),
        DroneInstance(vehicle_id="drone_02", role=DroneRole.SERVICER, system_id=2, instance_id=1, spawn_north_m=2),
    ])


class AssignmentState(StrEnum):
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    APPROACHING = "APPROACHING"
    HOLDING_FOR_RELEASE = "HOLDING_FOR_RELEASE"
    RELEASED = "RELEASED"
    VERIFYING = "VERIFYING"
    DELIVERED = "DELIVERED"
    RESUMING_SEARCH = "RESUMING_SEARCH"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ServicePriority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class PrioritySource(StrEnum):
    DEFAULT = "DEFAULT"
    OPERATOR = "OPERATOR"
    MISSION = "MISSION"


class SurvivorObservation(BaseModel):
    session_id: UUID
    mission_id: str
    vehicle_id: str
    frame_id: str
    observed_at: datetime
    class_id: int = 0
    confidence: float = Field(ge=0, le=1)
    bbox_xywh: tuple[float, float, float, float] = (0, 0, 0, 0)
    latitude: float | None = None
    longitude: float | None = None
    east_m: float | None = None
    north_m: float | None = None
    uncertainty_m: float | None = Field(default=None, gt=0)
    observation_id: str | None = None
    track_id: str | None = None
    is_confirmed: bool = False


class SharedSurvivorRecord(BaseModel):
    canonical_id: str
    session_id: UUID
    confirmation_status: str = "CONFIRMED"
    observation_count: int = 1
    source_drone_ids: list[str] = Field(default_factory=list)
    source_track_ids: list[str] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    priority: ServicePriority = ServicePriority.NORMAL
    priority_source: PrioritySource = PrioritySource.DEFAULT
    priority_updated_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    east_m: float | None = None
    north_m: float | None = None
    uncertainty_m: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    inlier_count: int = 1
    evidence_references: list[str] = Field(default_factory=list)
    assignment_id: str | None = None
    assigned_servicer_id: str | None = None
    assignment_state: AssignmentState | str | None = None
    delivery_status: str | None = None


class ServiceAssignment(BaseModel):
    assignment_id: UUID
    session_id: UUID
    canonical_survivor_id: str
    servicer_id: str | None = None
    state: AssignmentState | str = AssignmentState.QUEUED
    reason: str | None = None
    priority: ServicePriority = ServicePriority.NORMAL
    priority_source: PrioritySource = PrioritySource.DEFAULT
    queue_position: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lease_expires_at: datetime | None = None

    # Phase 2B Autonomous Service Execution & Delivery Evidence
    payload_released: bool = False
    delivery_verified: bool = False
    release_id: str | None = None
    delivery_evidence: dict[str, Any] | None = None
    checkpoint_seq: int | None = None
    resume_seq: int | None = None
    approach_point: tuple[float, float, float] | None = None
    delivery_point: tuple[float, float, float] | None = None
    standoff_distance_m: float | None = None
    failure_reason: str | None = None
    state_history: list[dict[str, Any]] = Field(default_factory=list)


class DeliveryRecord(BaseModel):
    assignment_id: UUID
    session_id: UUID
    canonical_survivor_id: str
    vehicle_id: str
    verified_at: datetime | None = None
    state: str = "NOT_STARTED"


class SearchProgress(BaseModel):
    vehicle_id: str
    sector_ids: list[str]
    current_mission_item: int = 0
    total_mission_items: int = 0
    coverage_percent: float = Field(default=0, ge=0, le=100)


class FleetSession(BaseModel):
    session_id: UUID
    mission_id: str
    run_id: str
    state: str
    configuration: FleetConfiguration
    started_at: datetime
    ended_at: datetime | None = None


class DroneHealth(BaseModel):
    vehicle_id: str
    connected: bool = False
    telemetry_age_s: float | None = None
    gps_fix_type: int | None = None
    camera_frame_count: int = 0
    camera_age_s: float | None = None
    model_status: str = "WAITING"
    error: str | None = None


class DroneMissionState(BaseModel):
    vehicle_id: str
    state: str = "WAITING"
    progress: SearchProgress
    last_event: str | None = None
