"""Deterministic Engineering-only normalized fault injection."""

from __future__ import annotations

import random
from datetime import timedelta

from pydantic import BaseModel, Field

from .models import ComponentHealth, FailureInjection, FailureType, HealthState, SystemHealthSnapshot


class TransportFaultProfile(BaseModel):
    """Seeded transport impairment without nondeterministic sleeps in CI."""

    latency_ms: float = Field(default=0, ge=0)
    jitter_ms: float = Field(default=0, ge=0)
    packet_drop_probability: float = Field(default=0, ge=0, le=1)
    seed: int = 8

    def outcomes(self, count: int) -> list[tuple[bool, float]]:
        rng = random.Random(self.seed)
        outcomes: list[tuple[bool, float]] = []
        for _ in range(count):
            delivered = rng.random() >= self.packet_drop_probability
            jitter = rng.uniform(-self.jitter_ms, self.jitter_ms) if self.jitter_ms else 0
            outcomes.append((delivered, max(0.0, self.latency_ms + jitter)))
        return outcomes


class FailureInjector:
    def __init__(self, *, engineering_enabled: bool, competition_enabled: bool):
        self.engineering_enabled = engineering_enabled
        self.competition_enabled = competition_enabled
        self.active: dict[str, FailureInjection] = {}

    def inject(self, failure: FailureType, phase: str, **parameters: object) -> FailureInjection:
        if self.competition_enabled or not self.engineering_enabled:
            raise PermissionError("Failure injection is disabled outside Engineering Mode")
        injection = FailureInjection(failure_type=failure, phase=phase, parameters=parameters)
        self.active[injection.injection_id] = injection
        return injection

    def clear(self, injection_id: str) -> None:
        self.active.pop(injection_id, None)

    def apply(self, health: SystemHealthSnapshot, injection: FailureInjection) -> SystemHealthSnapshot:
        updated = health.model_copy(deep=True)
        failure = injection.failure_type
        component_states = {
            FailureType.MAVLINK_LINK_LOSS: ("mavlink", HealthState.FAILED),
            FailureType.VISION_PROCESS_CRASH: ("vision", HealthState.FAILED),
            FailureType.CAMERA_FREEZE: ("camera", HealthState.STALE),
            FailureType.DETECTOR_FREEZE: ("detector", HealthState.STALE),
            FailureType.GPS_DEGRADED: ("gps", HealthState.FAILED),
            FailureType.EKF_DEGRADED: ("ekf", HealthState.FAILED),
            FailureType.LOW_BATTERY: ("battery", HealthState.DEGRADED),
            FailureType.CRITICAL_BATTERY: ("battery", HealthState.FAILED),
            FailureType.GEOFENCE_BREACH: ("geofence", HealthState.FAILED),
            FailureType.DATABASE_UNAVAILABLE: ("database", HealthState.FAILED),
        }
        if failure in component_states:
            component, state = component_states[failure]
            item = updated.components.get(component) or ComponentHealth(component=component)
            item.state = state
            if state is HealthState.STALE:
                item.last_update = updated.timestamp - timedelta(seconds=item.freshness_timeout_s + 1)
            item.evidence.update({"injection_id": injection.injection_id, "method": injection.method})
            updated.components[component] = item
        elif failure is FailureType.GUIDED_COMMAND_STOP:
            updated.guided_command_age_s = float(injection.parameters.get("age_s", 5))
        elif failure is FailureType.MISSION_DEADLINE_PRESSURE:
            updated.mission_elapsed_s = updated.mission_deadline_s - float(injection.parameters.get("remaining_s", 30))
        elif failure is FailureType.GEOFENCE_THREAT:
            updated.boundary_distance_m = float(injection.parameters.get("distance_m", .5))
        elif failure is FailureType.EMERGENCY_RECALL:
            updated.native_failsafe_evidence.append("OPERATOR_EMERGENCY_RECALL")
        return updated

    @staticmethod
    def deterministic_delivery(seed: int, probability: float) -> bool:
        return random.Random(seed).random() >= probability
