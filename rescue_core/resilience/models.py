"""Normalized Stage 8S health, fault, recovery, and audit records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SafetySeverity(StrEnum):
    INFO = "INFO"
    DEGRADED = "DEGRADED"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class SafetyAction(StrEnum):
    CONTINUE = "CONTINUE"
    DEFER_TARGET = "DEFER_TARGET"
    ABORT_TARGET_SERVICE = "ABORT_TARGET_SERVICE"
    RESUME_SEARCH = "RESUME_SEARCH"
    REQUEST_RTL = "REQUEST_RTL"
    OBSERVE_AUTOPILOT_FAILSAFE = "OBSERVE_AUTOPILOT_FAILSAFE"
    MISSION_FAILED = "MISSION_FAILED"


class FailsafeOwnership(StrEnum):
    AUTOPILOT = "AUTOPILOT"
    AUTONOMY = "AUTONOMY"
    OPERATOR = "OPERATOR"


class HealthState(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class AggregateHealth(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    EMERGENCY = "EMERGENCY"


class FailureType(StrEnum):
    GCS_BROWSER_DISCONNECT = "GCS_BROWSER_DISCONNECT"
    GROUND_BACKEND_RESTART = "GROUND_BACKEND_RESTART"
    WEBSOCKET_DROP = "WEBSOCKET_DROP"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DISK_PRESSURE = "DISK_PRESSURE"
    MAVLINK_LINK_LOSS = "MAVLINK_LINK_LOSS"
    MAVLINK_HIGH_LATENCY = "MAVLINK_HIGH_LATENCY"
    MAVLINK_PACKET_DROP = "MAVLINK_PACKET_DROP"
    VISION_PROCESS_CRASH = "VISION_PROCESS_CRASH"
    CAMERA_FREEZE = "CAMERA_FREEZE"
    DETECTOR_FREEZE = "DETECTOR_FREEZE"
    STALE_DETECTION = "STALE_DETECTION"
    TARGET_LOSS = "TARGET_LOSS"
    GPS_DEGRADED = "GPS_DEGRADED"
    GPS_POSITION_JUMP = "GPS_POSITION_JUMP"
    EKF_DEGRADED = "EKF_DEGRADED"
    LOW_BATTERY = "LOW_BATTERY"
    CRITICAL_BATTERY = "CRITICAL_BATTERY"
    GEOFENCE_THREAT = "GEOFENCE_THREAT"
    GEOFENCE_BREACH = "GEOFENCE_BREACH"
    GUIDED_COMMAND_STOP = "GUIDED_COMMAND_STOP"
    PAYLOAD_RELEASE_FAILURE = "PAYLOAD_RELEASE_FAILURE"
    PAYLOAD_VERIFICATION_FAILURE = "PAYLOAD_VERIFICATION_FAILURE"
    MISSION_DEADLINE_PRESSURE = "MISSION_DEADLINE_PRESSURE"
    ORCHESTRATOR_CRASH = "ORCHESTRATOR_CRASH"
    EMERGENCY_RECALL = "EMERGENCY_RECALL"


class StoragePriority(StrEnum):
    CRITICAL = "CRITICAL"
    MISSION = "MISSION"
    DIAGNOSTIC = "DIAGNOSTIC"
    OPTIONAL = "OPTIONAL"


class ComponentHealth(BaseModel):
    component: str
    state: HealthState = HealthState.UNKNOWN
    last_update: datetime = Field(default_factory=lambda: datetime.now(UTC))
    freshness_timeout_s: float = Field(default=5, gt=0)
    required: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)

    def effective_state(self, now: datetime | None = None) -> HealthState:
        current = now or datetime.now(UTC)
        age = (current - self.last_update).total_seconds()
        return HealthState.STALE if age > self.freshness_timeout_s else self.state


class SystemHealthSnapshot(BaseModel):
    mission_id: str
    rescue_state: str = "IDLE"
    vehicle_mode: str = "UNKNOWN"
    previous_vehicle_mode: str | None = None
    armed: bool = False
    mission_elapsed_s: float = Field(default=0, ge=0)
    mission_deadline_s: float = Field(default=1800, gt=0)
    guided_command_age_s: float | None = Field(default=None, ge=0)
    boundary_distance_m: float | None = None
    components: dict[str, ComponentHealth] = Field(default_factory=dict)
    native_failsafe_evidence: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def state(self, component: str) -> HealthState:
        health = self.components.get(component)
        return health.effective_state(self.timestamp) if health else HealthState.UNKNOWN


class SafetyDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    severity: SafetySeverity
    reason: str
    source: str
    recommended_action: SafetyAction
    authoritative_layer: FailsafeOwnership
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence: dict[str, Any] = Field(default_factory=dict)


class MissionRecoverySnapshot(BaseModel):
    snapshot_version: int = 1
    mission_id: str
    mission_fingerprint: str
    execution_source: str
    orchestrator_state: str
    vehicle_id: str
    current_target_id: str | None = None
    search_checkpoint: dict[str, Any] | None = None
    payload_inventory: list[dict[str, Any]] = Field(default_factory=list)
    delivered_target_ids: list[str] = Field(default_factory=list)
    delivery_attempts: list[dict[str, Any]] = Field(default_factory=list)
    current_mission_sequence: int = Field(ge=0)
    coverage_percent: float = Field(ge=0, le=100)
    last_confirmed_vehicle_mode: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RecoveryDisposition(StrEnum):
    RECONSTRUCT_AUTO = "RECONSTRUCT_AUTO"
    OBSERVE_RETURNING = "OBSERVE_RETURNING"
    OBSERVE_LANDING = "OBSERVE_LANDING"
    REQUEST_RTL = "REQUEST_RTL"
    FAILED_SAFE = "FAILED_SAFE"


class RecoveryResult(BaseModel):
    disposition: RecoveryDisposition
    reconstructed_state: str
    may_resume_delivery: bool = False
    reason: str
    actual_mode_authoritative: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)


class FailureInjection(BaseModel):
    injection_id: str = Field(default_factory=lambda: str(uuid4()))
    failure_type: FailureType
    phase: str
    method: str = "NORMALIZED_EVENT"
    parameters: dict[str, Any] = Field(default_factory=dict)
    injected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seed: int = 8


class ResilienceScenario(BaseModel):
    scenario_id: str
    description: str
    group: str
    phase: str
    critical: bool = False
    failure: FailureType
    injection_method: str = "NORMALIZED_EVENT"
    expected_action: SafetyAction
    expected_final_states: list[str]
    drop_permitted: bool = False
    maximum_recovery_time_s: float = Field(default=10, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ScenarioResult(BaseModel):
    scenario_id: str
    group: str
    critical: bool
    phase: str
    injection_method: str
    detected: bool
    decision: SafetyDecision
    drop_prevented: bool
    final_state: str
    recovered: bool
    detection_latency_ms: float = Field(ge=0)
    decision_latency_ms: float = Field(ge=0)
    safe_mode_latency_ms: float | None = Field(default=None, ge=0)
    violations: list[str] = Field(default_factory=list)
    passed: bool


class OperatorAction(StrEnum):
    MISSION_START = "MISSION_START"
    SAFETY_ABORT = "SAFETY_ABORT"
    EMERGENCY_RECALL = "EMERGENCY_RECALL"
    MANUAL_WAYPOINT = "MANUAL_WAYPOINT"
    MANUAL_GOTO = "MANUAL_GOTO"
    MANUAL_TARGET_CONFIRM = "MANUAL_TARGET_CONFIRM"
    MANUAL_TARGET_DELETE = "MANUAL_TARGET_DELETE"
    MANUAL_PAYLOAD_DROP = "MANUAL_PAYLOAD_DROP"
    MANUAL_RESUME_SEQUENCE = "MANUAL_RESUME_SEQUENCE"
    MANUAL_FLIGHT_MODE = "MANUAL_FLIGHT_MODE"
    MANUAL_FLIGHT_PATH_ADJUSTMENT = "MANUAL_FLIGHT_PATH_ADJUSTMENT"


class OperatorActionEvent(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    mission_id: str
    action: OperatorAction
    mission_state: str
    allowed: bool
    rejected_reason: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReadinessStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReadinessCheck(BaseModel):
    category: str
    status: ReadinessStatus
    reason: str
    software_evidence: str | None = None
    sitl_evidence: str | None = None
    physical_evidence: str | None = None


class DeliveryAttemptState(StrEnum):
    RESERVED = "RESERVED"
    RELEASE_REQUESTED = "RELEASE_REQUESTED"
    RELEASED = "RELEASED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class DeliveryAttempt(BaseModel):
    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    target_id: str
    payload_slot: str
    state: DeliveryAttemptState = DeliveryAttemptState.RESERVED
    release_command_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InvariantCheck(BaseModel):
    name: str
    passed: bool
    severity: SafetySeverity = SafetySeverity.CRITICAL
    detail: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
