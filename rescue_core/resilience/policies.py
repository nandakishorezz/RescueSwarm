"""Deadline, operator, readiness, delivery-idempotency, and boundary policy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon

from rescue_core.common.hardware_gates import H1_PHYSICAL_PROTOTYPE

from .models import (
    DeliveryAttempt,
    DeliveryAttemptState,
    OperatorAction,
    OperatorActionEvent,
    ReadinessCheck,
    ReadinessStatus,
)


class MissionDeadlinePolicy:
    def __init__(self, maximum_duration_s: float, rtl_reserve_s: float, service_estimate_s: float):
        if min(maximum_duration_s, rtl_reserve_s, service_estimate_s) <= 0:
            raise ValueError("Deadline policy values must be positive")
        self.maximum_duration_s = maximum_duration_s
        self.rtl_reserve_s = rtl_reserve_s
        self.service_estimate_s = service_estimate_s

    def may_start_service(self, elapsed_s: float, estimated_travel_s: float = 0) -> tuple[bool, str]:
        required = estimated_travel_s + self.service_estimate_s + self.rtl_reserve_s
        remaining = self.maximum_duration_s - elapsed_s
        return (remaining >= required, f"remaining={remaining:.1f}s required={required:.1f}s")

    def active_service_action(self, elapsed_s: float) -> str:
        return "ABORT_TARGET_SERVICE" if self.maximum_duration_s - elapsed_s <= self.rtl_reserve_s else "CONTINUE"


class DetectionFreshnessPolicy:
    def __init__(self, maximum_age_s: float):
        if maximum_age_s <= 0:
            raise ValueError("Detection freshness must be positive")
        self.maximum_age_s = maximum_age_s

    def accepts(self, capture_time: datetime, now: datetime | None = None) -> tuple[bool, float]:
        current = now or datetime.now(UTC)
        age = max(0.0, (current - capture_time).total_seconds())
        return age <= self.maximum_age_s, age


class OperatorActionPolicy:
    ALLOWED_COMPETITION = {
        OperatorAction.MISSION_START,
        OperatorAction.SAFETY_ABORT,
        OperatorAction.EMERGENCY_RECALL,
    }

    def evaluate(
        self, mission_id: str, action: OperatorAction, mission_state: str,
        *, competition_enabled: bool,
    ) -> OperatorActionEvent:
        # Engineering diagnostics use separate, explicitly named endpoints.
        # This operator surface never grows manual flight or payload authority.
        allowed = action in self.ALLOWED_COMPETITION
        if competition_enabled and action is OperatorAction.MISSION_START and mission_state not in {"READY", "PLAN_READY"}:
            allowed = False
        return OperatorActionEvent(
            mission_id=mission_id, action=action, mission_state=mission_state,
            allowed=allowed,
            rejected_reason=None if allowed else "Manual mission/flight/payload action is not implemented",
        )


class DeliveryAttemptLedger:
    """Exactly-once logical delivery semantics, ready for future actuator tokens."""

    def __init__(self, attempts: list[DeliveryAttempt] | None = None):
        self.attempts = {item.attempt_id: item for item in attempts or []}

    def begin(self, target_id: str, payload_slot: str) -> DeliveryAttempt:
        active = [item for item in self.attempts.values() if item.target_id == target_id and item.state not in {DeliveryAttemptState.FAILED, DeliveryAttemptState.UNKNOWN}]
        if active:
            return active[0]
        attempt = DeliveryAttempt(target_id=target_id, payload_slot=payload_slot)
        self.attempts[attempt.attempt_id] = attempt
        return attempt

    def transition(self, attempt_id: str, state: DeliveryAttemptState, command_id: str | None = None) -> DeliveryAttempt:
        attempt = self.attempts[attempt_id]
        allowed = {
            DeliveryAttemptState.RESERVED: {DeliveryAttemptState.RELEASE_REQUESTED, DeliveryAttemptState.FAILED},
            DeliveryAttemptState.RELEASE_REQUESTED: {DeliveryAttemptState.RELEASED, DeliveryAttemptState.FAILED, DeliveryAttemptState.UNKNOWN},
            DeliveryAttemptState.RELEASED: {DeliveryAttemptState.VERIFIED, DeliveryAttemptState.FAILED, DeliveryAttemptState.UNKNOWN},
        }
        if state == attempt.state:
            return attempt
        if state not in allowed.get(attempt.state, set()):
            raise ValueError(f"Invalid delivery attempt transition: {attempt.state} -> {state}")
        if state is DeliveryAttemptState.RELEASE_REQUESTED:
            if not command_id:
                raise ValueError("Release requires an idempotency command ID")
            attempt.release_command_id = command_id
        attempt.state, attempt.updated_at = state, datetime.now(UTC)
        return attempt

    def delivered_targets(self) -> set[str]:
        return {item.target_id for item in self.attempts.values() if item.state is DeliveryAttemptState.VERIFIED}


class BoundarySafetyMonitor:
    def __init__(self, region: Polygon, warning_margin_m: float):
        self.region, self.warning_margin_m = region, warning_margin_m

    def distance(self, east_m: float, north_m: float) -> float:
        point = Point(east_m, north_m)
        distance = point.distance(self.region.boundary)
        return distance if self.region.covers(point) else -distance

    def status(self, east_m: float, north_m: float) -> str:
        distance = self.distance(east_m, north_m)
        if distance < 0:
            return "BREACH"
        if distance < self.warning_margin_m:
            return "MARGIN_LOW"
        return "SAFE"


class CompetitionReadinessValidator:
    def evaluate(self, *, offline_pass: bool = True, include_stage9s: bool = False) -> list[ReadinessCheck]:
        return [
            ReadinessCheck(category="MISSION INPUT", status=ReadinessStatus.PASS, reason="Offline boundary validation and planning implemented", software_evidence="Stages 2/3"),
            ReadinessCheck(category="AUTONOMY", status=ReadinessStatus.PASS, reason="Single-drone rescue flow and resilience policies pass in SITL", sitl_evidence="Stages 7S/8S"),
            ReadinessCheck(category="GCS", status=ReadinessStatus.PASS, reason="One offline operator interface with competition lock"),
            ReadinessCheck(category="NETWORK", status=ReadinessStatus.PASS if offline_pass else ReadinessStatus.FAIL, reason="Offline compliance scanner result"),
            ReadinessCheck(category="FAILSAFES", status=ReadinessStatus.PENDING, reason="SITL evidence exists; physical failsafes remain unverified", physical_evidence="NONE"),
            ReadinessCheck(category="VIDEO", status=ReadinessStatus.PENDING, reason="Live video is simulated/not physically verified"),
            ReadinessCheck(category="PAYLOAD", status=ReadinessStatus.PENDING, reason="Payload release and feedback are simulated only"),
            ReadinessCheck(category="HARDWARE", status=ReadinessStatus.PENDING, reason=f"{H1_PHYSICAL_PROTOTYPE.gate_id} is {H1_PHYSICAL_PROTOTYPE.state}", physical_evidence="NONE"),
            ReadinessCheck(
                category="FLEET",
                status=ReadinessStatus.PASS if include_stage9s else ReadinessStatus.FAIL,
                reason=(
                    "Multi-drone coordination, area division, target sharing, and delivery allocation pass in SITL"
                    if include_stage9s else "Two-or-more-drone fleet evidence was not included in this Stage 8S assessment"
                ),
                sitl_evidence="Stage 9S" if include_stage9s else None,
            ),
            ReadinessCheck(category="MASS", status=ReadinessStatus.PENDING, reason="25 kg combined mass not measured"),
            ReadinessCheck(category="LAUNCH AREA", status=ReadinessStatus.PENDING, reason="12 ft x 12 ft compliance not physically tested"),
        ]

    def overall(self, checks: list[ReadinessCheck]) -> str:
        return "NOT_COMPETITION_READY" if any(item.status in {ReadinessStatus.FAIL, ReadinessStatus.PENDING} for item in checks) else "READY"
