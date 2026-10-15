"""Authoritative state machine for fleet lifecycle and per-drone states."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


class FleetState(StrEnum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    STARTING = "STARTING"
    WAITING_FOR_TELEMETRY = "WAITING_FOR_TELEMETRY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    RETURNING = "RETURNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class DroneLifecycleState(StrEnum):
    CONFIGURED = "CONFIGURED"
    STARTING = "STARTING"
    WAITING_FOR_HEARTBEAT = "WAITING_FOR_HEARTBEAT"
    READY = "READY"
    SEARCHING = "SEARCHING"
    APPROACHING = "APPROACHING"
    HOLDING_FOR_RELEASE = "HOLDING_FOR_RELEASE"
    RELEASED = "RELEASED"
    VERIFYING = "VERIFYING"
    DELIVERED = "DELIVERED"
    RESUMING_SEARCH = "RESUMING_SEARCH"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    LANDED = "LANDED"
    DISARMED = "DISARMED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


FLEET_STATE_EXPLANATIONS: dict[FleetState, str] = {
    FleetState.IDLE: "No active fleet session",
    FleetState.VALIDATING: "Checking configuration",
    FleetState.STARTING: "Launching vehicles",
    FleetState.WAITING_FOR_TELEMETRY: "Waiting for vehicle connections",
    FleetState.RUNNING: "Fleet active",
    FleetState.DEGRADED: "One or more components need attention",
    FleetState.RETURNING: "Vehicles returning safely",
    FleetState.STOPPING: "Cleaning up simulation resources",
    FleetState.STOPPED: "Fleet stopped",
    FleetState.ERROR: "Startup or runtime failure",
}


VALID_FLEET_TRANSITIONS: dict[FleetState, set[FleetState]] = {
    FleetState.IDLE: {FleetState.VALIDATING, FleetState.ERROR},
    FleetState.VALIDATING: {FleetState.STARTING, FleetState.ERROR, FleetState.IDLE},
    FleetState.STARTING: {FleetState.WAITING_FOR_TELEMETRY, FleetState.RETURNING, FleetState.STOPPING, FleetState.ERROR},
    FleetState.WAITING_FOR_TELEMETRY: {FleetState.RUNNING, FleetState.DEGRADED, FleetState.RETURNING, FleetState.STOPPING, FleetState.ERROR},
    FleetState.RUNNING: {FleetState.DEGRADED, FleetState.RETURNING, FleetState.STOPPING, FleetState.ERROR},
    FleetState.DEGRADED: {FleetState.RUNNING, FleetState.RETURNING, FleetState.STOPPING, FleetState.ERROR},
    FleetState.RETURNING: {FleetState.STOPPING, FleetState.DEGRADED, FleetState.ERROR},
    FleetState.STOPPING: {FleetState.STOPPED, FleetState.ERROR},
    FleetState.STOPPED: {FleetState.IDLE, FleetState.VALIDATING},
    FleetState.ERROR: {FleetState.IDLE, FleetState.VALIDATING},
}


VALID_DRONE_TRANSITIONS: dict[DroneLifecycleState, set[DroneLifecycleState]] = {
    DroneLifecycleState.CONFIGURED: {DroneLifecycleState.STARTING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.STARTING: {DroneLifecycleState.WAITING_FOR_HEARTBEAT, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.WAITING_FOR_HEARTBEAT: {DroneLifecycleState.READY, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.READY: {DroneLifecycleState.SEARCHING, DroneLifecycleState.APPROACHING, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.SEARCHING: {DroneLifecycleState.APPROACHING, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.APPROACHING: {DroneLifecycleState.HOLDING_FOR_RELEASE, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.HOLDING_FOR_RELEASE: {DroneLifecycleState.RELEASED, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.RELEASED: {DroneLifecycleState.VERIFYING, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.VERIFYING: {DroneLifecycleState.DELIVERED, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.DELIVERED: {DroneLifecycleState.RESUMING_SEARCH, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.RESUMING_SEARCH: {DroneLifecycleState.SEARCHING, DroneLifecycleState.RETURNING, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.RETURNING: {DroneLifecycleState.LANDING, DroneLifecycleState.LANDED, DroneLifecycleState.DISARMED, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.LANDING: {DroneLifecycleState.LANDED, DroneLifecycleState.DISARMED, DroneLifecycleState.FAILED, DroneLifecycleState.STOPPED},
    DroneLifecycleState.LANDED: {DroneLifecycleState.DISARMED, DroneLifecycleState.STOPPED, DroneLifecycleState.FAILED},
    DroneLifecycleState.DISARMED: {DroneLifecycleState.STOPPED, DroneLifecycleState.FAILED},
    DroneLifecycleState.FAILED: {DroneLifecycleState.STOPPED, DroneLifecycleState.CONFIGURED},
    DroneLifecycleState.STOPPED: {DroneLifecycleState.CONFIGURED, DroneLifecycleState.STARTING},
}


class FleetStateMachine:
    def __init__(self, initial: FleetState = FleetState.IDLE):
        self._current: FleetState = initial
        self._updated_at: datetime = datetime.now(UTC)
        self._reason: str = FLEET_STATE_EXPLANATIONS.get(initial, "")

    @property
    def current(self) -> FleetState:
        return self._current

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def reason(self) -> str:
        return self._reason

    @reason.setter
    def reason(self, value: str) -> None:
        self._reason = value

    def can_transition(self, target: FleetState) -> bool:
        if target == self._current:
            return True
        return target in VALID_FLEET_TRANSITIONS.get(self._current, set())

    def transition(self, target: FleetState, reason: str | None = None) -> FleetState:
        if target == self._current:
            if reason:
                self._reason = reason
            return self._current
        valid_targets = VALID_FLEET_TRANSITIONS.get(self._current, set())
        if target not in valid_targets:
            raise ValueError(
                f"Invalid fleet state transition from {self._current} to {target}. "
                f"Valid targets: {sorted(t.value for t in valid_targets)}"
            )
        self._current = target
        self._updated_at = datetime.now(UTC)
        self._reason = reason or FLEET_STATE_EXPLANATIONS.get(target, "")
        return self._current


class DroneStateMachine:
    def __init__(self, vehicle_id: str, initial: DroneLifecycleState = DroneLifecycleState.CONFIGURED):
        self.vehicle_id = vehicle_id
        self._current: DroneLifecycleState = initial
        self._updated_at: datetime = datetime.now(UTC)
        self._error: str | None = None

    @property
    def current(self) -> DroneLifecycleState:
        return self._current

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def error(self) -> str | None:
        return self._error

    def can_transition(self, target: DroneLifecycleState) -> bool:
        if target == self._current:
            return True
        return target in VALID_DRONE_TRANSITIONS.get(self._current, set())

    def transition(self, target: DroneLifecycleState, error: str | None = None) -> DroneLifecycleState:
        if target == self._current:
            if error is not None:
                self._error = error
            return self._current
        valid_targets = VALID_DRONE_TRANSITIONS.get(self._current, set())
        if target not in valid_targets:
            raise ValueError(
                f"Invalid drone state transition for {self.vehicle_id} from {self._current} to {target}. "
                f"Valid targets: {sorted(t.value for t in valid_targets)}"
            )
        self._current = target
        self._updated_at = datetime.now(UTC)
        if error is not None:
            self._error = error
        return self._current


__all__ = [
    "FleetState",
    "DroneLifecycleState",
    "FLEET_STATE_EXPLANATIONS",
    "VALID_FLEET_TRANSITIONS",
    "VALID_DRONE_TRANSITIONS",
    "FleetStateMachine",
    "DroneStateMachine",
]
