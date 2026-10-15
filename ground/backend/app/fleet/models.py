"""Persistable Stage 9S fleet models; no transport or flight commands live here."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ..planning.models import CoveragePlan, EnuCoordinate, PathKind


class HardwareGateStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"


class VehicleFleetState(StrEnum):
    REGISTERED = "REGISTERED"
    READY = "READY"
    LAUNCH_QUEUED = "LAUNCH_QUEUED"
    LAUNCHING = "LAUNCHING"
    SEARCHING = "SEARCHING"
    TARGET_SERVICE = "TARGET_SERVICE"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    LANDED = "LANDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class FleetState(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    PARTITIONING = "PARTITIONING"
    READY = "READY"
    LAUNCHING = "LAUNCHING"
    SEARCHING = "SEARCHING"
    SERVICING_TARGETS = "SERVICING_TARGETS"
    REALLOCATING = "REALLOCATING"
    RECALLING = "RECALLING"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class CoverageWorkState(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    REASSIGN_REQUIRED = "REASSIGN_REQUIRED"


class AssignmentLeaseState(StrEnum):
    PROPOSED = "PROPOSED"
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"
    AMBIGUOUS = "AMBIGUOUS"


class SeparationState(StrEnum):
    CLEAR = "CLEAR"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CoverageSegmentWork(BaseModel):
    segment_id: str
    kind: PathKind
    points_enu: list[EnuCoordinate]
    search_distance_m: float = Field(ge=0)
    transit_distance_m: float = Field(ge=0)
    estimated_search_time_s: float = Field(ge=0)
    turn_cost_m: float = Field(ge=0)
    total_cost: float = Field(ge=0)
    entry_point: EnuCoordinate
    exit_point: EnuCoordinate
    adjacent_segment_ids: list[str] = Field(default_factory=list)
    assigned_vehicle_id: str | None = None
    state: CoverageWorkState = CoverageWorkState.UNASSIGNED


class SectorAssignment(BaseModel):
    assignment_id: str = Field(default_factory=lambda: str(uuid4()))
    vehicle_id: str
    segment_ids: list[str]
    estimated_cost: float = Field(ge=0)
    estimated_search_time_s: float = Field(ge=0)
    state: CoverageWorkState = CoverageWorkState.ASSIGNED
    version: int = Field(default=1, ge=1)
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class PartitionMetrics(BaseModel):
    total_cost: float = Field(ge=0)
    per_vehicle_cost: dict[str, float]
    maximum_vehicle_cost: float = Field(ge=0)
    minimum_vehicle_cost: float = Field(ge=0)
    imbalance_percent: float = Field(ge=0)
    transit_distance_m: float = Field(ge=0)
    cross_sector_transit_m: float = Field(ge=0)
    disconnected_assignments: int = Field(ge=0)
    estimated_makespan_s: float = Field(ge=0)


class FleetPlan(BaseModel):
    mission_id: str
    mission_fingerprint: str
    partition_fingerprint: str
    fleet_size: int = Field(ge=2)
    vehicle_ids: list[str]
    global_coverage_plan: CoveragePlan
    segments: list[CoverageSegmentWork]
    assignments: list[SectorAssignment]
    vehicle_plans: dict[str, CoveragePlan]
    launch_order: list[str]
    metrics: PartitionMetrics
    search_altitude_evidence: str = "SITL_ENGINEERING_DEFAULT"
    hardware_gate_h1: HardwareGateStatus = HardwareGateStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    validation_errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fleet(self) -> "FleetPlan":
        if len(set(self.vehicle_ids)) != len(self.vehicle_ids):
            raise ValueError("Fleet vehicle IDs must be unique")
        if len(self.vehicle_ids) != self.fleet_size:
            raise ValueError("fleet_size must match vehicle_ids")
        return self

    @property
    def safe_polygon_enu(self) -> list[EnuCoordinate]:
        return self.global_coverage_plan.safe_polygon_enu


class FleetVehicle(BaseModel):
    vehicle_id: str
    system_id: int = Field(ge=1, le=255)
    sitl_instance_id: int = Field(ge=0)
    connection: str
    state: VehicleFleetState = VehicleFleetState.REGISTERED
    connected: bool = False
    armed: bool = False
    mode: str = "UNKNOWN"
    east_m: float | None = None
    north_m: float | None = None
    altitude_m: float | None = None
    velocity_m_s: float | None = None
    battery_percent: float | None = None
    gps_healthy: bool | None = None
    ekf_healthy: bool | None = None
    native_failsafe: bool = False
    current_sector_id: str | None = None
    current_task: str | None = None
    payload_remaining: int = Field(default=0, ge=0)
    current_target_id: str | None = None
    search_progress_percent: float = Field(default=0, ge=0, le=100)
    last_update_monotonic_ns: int = 0
    vehicle_sequence: int = Field(default=0, ge=0)
    autopilot_version: str | None = None
    autopilot_commit: str | None = None


class AssignmentLease(BaseModel):
    assignment_id: str = Field(default_factory=lambda: str(uuid4()))
    target_id: str
    vehicle_id: str
    payload_slot: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    assignment_version: int = Field(default=1, ge=1)
    state: AssignmentLeaseState = AssignmentLeaseState.PROPOSED


class CanonicalFleetTarget(BaseModel):
    target_id: str
    mission_id: str
    east_m: float
    north_m: float
    latitude: float
    longitude: float
    uncertainty_m: float = Field(gt=0)
    state: str = "CONFIRMED"
    delivery_state: str = "PENDING"
    observing_vehicle_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    detection_confidence: float = Field(ge=0, le=1)
    first_seen_at: datetime
    last_seen_at: datetime
    assigned_vehicle_id: str | None = None
    assignment_id: str | None = None
    assignment_version: int = 0
    simulated: bool = True


class LaunchLandingZone(BaseModel):
    width_m: float = Field(default=3.6576, gt=0)
    length_m: float = Field(default=3.6576, gt=0)
    center_east_m: float = 0
    center_north_m: float = 0
    evidence: str = "REFERENCE_POSITION_ONLY_SITL"


class SeparationAssessment(BaseModel):
    vehicle_a: str
    vehicle_b: str
    horizontal_distance_m: float = Field(ge=0)
    vertical_separation_m: float = Field(ge=0)
    state: SeparationState
    predicted_closest_approach_m: float | None = Field(default=None, ge=0)


class FleetRecoverySnapshot(BaseModel):
    mission_fingerprint: str
    fleet_plan: dict[str, Any]
    vehicle_registry: dict[str, Any]
    sector_assignments: list[dict[str, Any]]
    completed_segment_ids: list[str]
    uncompleted_segment_ids: list[str]
    target_registry: list[dict[str, Any]]
    assignment_leases: list[dict[str, Any]]
    payload_inventory: dict[str, int]
    launch_landing_schedule: dict[str, Any]
    fleet_state: FleetState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FleetPayloadCapacity(BaseModel):
    """Fleet-wide logical payload accounting; it makes no physical lift claim."""

    starting_by_vehicle: dict[str, int]
    remaining_by_vehicle: dict[str, int]
    reserved: int = Field(default=0, ge=0)
    released: int = Field(default=0, ge=0)
    verified: int = Field(default=0, ge=0)
    evidence: str = "SIMULATED_PAYLOAD_CAPACITY_ONLY"

    @model_validator(mode="after")
    def validate_capacity(self) -> "FleetPayloadCapacity":
        if set(self.starting_by_vehicle) != set(self.remaining_by_vehicle):
            raise ValueError("Payload starting/remaining vehicle sets must match")
        for vehicle_id, starting in self.starting_by_vehicle.items():
            remaining = self.remaining_by_vehicle[vehicle_id]
            if starting < 0 or remaining < 0 or remaining > starting:
                raise ValueError(f"Invalid payload capacity for {vehicle_id}")
        if self.verified > self.released:
            raise ValueError("Verified payloads cannot exceed released payloads")
        return self

    @property
    def total_starting(self) -> int:
        return sum(self.starting_by_vehicle.values())

    @property
    def total_remaining(self) -> int:
        return sum(self.remaining_by_vehicle.values())


class FleetMassItem(BaseModel):
    vehicle_id: str
    dry_mass_kg: float | None = Field(default=None, ge=0)
    battery_mass_kg: float | None = Field(default=None, ge=0)
    compute_mass_kg: float | None = Field(default=None, ge=0)
    sensor_mass_kg: float | None = Field(default=None, ge=0)
    payload_mechanism_mass_kg: float | None = Field(default=None, ge=0)
    payload_mass_kg: float | None = Field(default=None, ge=0)
    other_mass_kg: float | None = Field(default=None, ge=0)


class FleetMassManifest(BaseModel):
    vehicles: list[FleetMassItem]
    evidence_status: str = "UNKNOWN_NOT_MEASURED"

    @property
    def combined_all_up_mass_kg(self) -> float | None:
        fields = (
            "dry_mass_kg", "battery_mass_kg", "compute_mass_kg", "sensor_mass_kg",
            "payload_mechanism_mass_kg", "payload_mass_kg", "other_mass_kg",
        )
        values = [getattr(vehicle, field) for vehicle in self.vehicles for field in fields]
        return None if any(value is None for value in values) else float(sum(values))
