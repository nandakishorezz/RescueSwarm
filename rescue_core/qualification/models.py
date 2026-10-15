"""Evidence models used only to qualify the frozen Stage 1-9 architecture."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MissionOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    ABORTED_SAFE = "ABORTED_SAFE"
    FAILED_SAFE = "FAILED_SAFE"
    CAPACITY_LIMITED = "CAPACITY_LIMITED"
    DEADLINE_LIMITED = "DEADLINE_LIMITED"
    VEHICLE_LIMITED = "VEHICLE_LIMITED"


class PreflightStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class CompetitionRules(BaseModel):
    max_search_area_hectares: float = Field(gt=0)
    max_survivors: int = Field(ge=1)
    minimum_drones: int = Field(ge=2)
    max_mission_flight_time_s: float = Field(gt=0)
    max_setup_time_s: float = Field(gt=0)
    external_network_allowed: bool = False
    allowed_operator_actions: list[str]


class PhysicalRequirements(BaseModel):
    evidence_status: str
    combined_auw_limit_kg: float = Field(gt=0)
    payload_mass_g: float = Field(gt=0)
    payload_size_cm: dict[str, float]
    launch_landing_zone_ft: dict[str, float]


class QualificationDefaults(BaseModel):
    primary_fleet_size: int = Field(ge=2)
    smoke_fleet_size: int = Field(ge=3)
    coverage_requirement_percent: float = Field(gt=0, le=100)
    rtl_reserve_s: float = Field(gt=0)
    landing_queue_reserve_s: float = Field(ge=0)
    target_service_estimate_s: float = Field(gt=0)
    deadline_safety_factor: float = Field(ge=1)
    setup_benchmark_iterations: int = Field(ge=2)
    maximum_websocket_pending_messages: int = Field(ge=1)


class CompetitionScaleProfile(BaseModel):
    profile_id: str
    evidence_class: str
    competition: CompetitionRules
    physical_requirements: PhysicalRequirements
    qualification: QualificationDefaults
    hardware_gate_h1: str

    @model_validator(mode="after")
    def preserve_truth_boundaries(self) -> "CompetitionScaleProfile":
        if self.hardware_gate_h1 != "PENDING":
            raise ValueError("Stage 10S must preserve Hardware Gate H1=PENDING")
        if self.evidence_class != "SOFTWARE_SITL_ONLY":
            raise ValueError("Competition profile must remain SOFTWARE_SITL_ONLY")
        if self.competition.external_network_allowed:
            raise ValueError("Competition runtime must remain offline")
        return self


class MissionObjectiveSummary(BaseModel):
    coverage_required_percent: float = Field(ge=0, le=100)
    coverage_actual_percent: float = Field(ge=0, le=100)
    targets_discovered: int = Field(ge=0)
    targets_confirmed: int = Field(ge=0)
    targets_serviceable: int = Field(ge=0)
    targets_delivered: int = Field(ge=0)
    targets_unresolved: int = Field(ge=0)
    payload_starting: int = Field(ge=0)
    payload_remaining: int = Field(ge=0)
    vehicles_available: int = Field(ge=0)
    vehicles_terminal_safe: int = Field(ge=0)
    vehicles_deployed: int = Field(ge=0)
    deadline_reached: bool = False
    mission_aborted: bool = False
    critical_failure: bool = False
    outcome: MissionOutcome
    reasons: list[str] = Field(default_factory=list)


class DeadlineDecision(BaseModel):
    action: str
    remaining_mission_s: float
    estimated_remaining_search_s: float
    estimated_target_service_s: float
    rtl_reserve_s: float
    landing_reserve_s: float
    safety_factor: float
    required_s: float
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PreflightCheck(BaseModel):
    name: str
    status: PreflightStatus
    reason: str
    evidence_class: str = "SOFTWARE"

