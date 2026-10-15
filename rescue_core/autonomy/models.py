"""Normalized Stage 7S rescue orchestration records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SearchResumePolicyName(StrEnum):
    REPLAY_FROM_LAST_REACHED = "REPLAY_FROM_LAST_REACHED"
    ARDUPILOT_NATIVE_RESUME = "ARDUPILOT_NATIVE_RESUME"


class SearchResumeCheckpoint(BaseModel):
    mission_id: str
    mission_fingerprint: str
    current_mission_seq: int = Field(ge=0)
    last_reached_mission_seq: int = Field(ge=0)
    current_search_segment: str | None = None
    vehicle_east_m: float
    vehicle_north_m: float
    coverage_percent: float = Field(ge=0, le=100)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SyntheticSurvivorScenario(BaseModel):
    target_id: str
    true_east_m: float
    true_north_m: float
    true_latitude: float | None = Field(default=None, ge=-90, le=90)
    true_longitude: float | None = Field(default=None, ge=-180, le=180)
    visibility_distance_m: float = Field(default=40, gt=0)
    detection_probability: float = Field(default=1, ge=0, le=1)
    pixel_noise_stddev: float = Field(default=0, ge=0)
    coarse_estimate_offset_east_m: float = 0
    coarse_estimate_offset_north_m: float = 0
    delivery_required: bool = True
    seed: int = 7


class ReacquisitionObservation(BaseModel):
    timestamp_monotonic_ns: int
    target_id: str
    visible: bool
    relative_east_m: float | None = None
    relative_north_m: float | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    quality: str = "UNKNOWN"
    source: str = "SYNTHETIC_REACQUISITION"


class PayloadState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PayloadSlot(BaseModel):
    slot_id: str
    state: PayloadState = PayloadState.AVAILABLE
    target_id: str | None = None


class DeliveryEvidence(BaseModel):
    target_id: str
    payload_slot: str
    execution_source: str = "SITL_SIMULATED_PAYLOAD"
    release_requested_at: datetime
    release_completed_at: datetime | None = None
    verification_at: datetime | None = None
    vehicle_east_m: float
    vehicle_north_m: float
    relative_target_error_m: float = Field(ge=0)
    altitude_m: float = Field(ge=0)
    ground_speed_m_s: float = Field(ge=0)
    target_uncertainty_m: float = Field(ge=0)
    release_succeeded: bool = False
    verification_succeeded: bool = False
    release_command_id: str | None = None
    release_sim_time_ns: int | None = Field(default=None, ge=0)
    actuator_acknowledged: bool = False
    mechanism_acknowledged: bool = False
    verification_kind: str = "ACTUATOR_AND_MECHANISM_ONLY"


class RescueMetrics(BaseModel):
    time_to_first_detection_s: float | None = None
    detection_to_confirmation_s: float | None = None
    confirmation_to_guided_s: float | None = None
    target_approach_time_s: float | None = None
    reacquisition_time_s: float | None = None
    alignment_time_s: float | None = None
    delivery_time_s: float | None = None
    resume_time_s: float | None = None
    service_total_time_s: float | None = None
    search_interruption_overhead_s: float | None = None
    planned_search_distance_m: float = 0
    actual_search_distance_m: float = 0
    guided_diversion_distance_m: float = 0
    total_distance_m: float = 0
    final_search_coverage_percent: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
