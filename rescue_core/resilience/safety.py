"""Central safety decisions that yield to native ArduPilot failsafes."""

from __future__ import annotations

from .models import (
    AggregateHealth,
    FailsafeOwnership,
    HealthState,
    SafetyAction,
    SafetyDecision,
    SafetySeverity,
    SystemHealthSnapshot,
)


TARGET_SERVICE_STATES = {
    "SEARCH_SUSPENDING", "APPROACH_PLANNING", "GUIDED_APPROACH", "REACQUIRING",
    "PRECISION_ALIGNING", "DROP_READY", "DELIVERING", "DELIVERY_VERIFYING",
}
NATIVE_FAILSAFE_MODES = {"RTL", "LAND", "SMART_RTL"}


class SafetySupervisor:
    """Evaluate one immutable health snapshot; it sends no commands itself."""

    def evaluate(self, health: SystemHealthSnapshot) -> SafetyDecision:
        in_service = health.rescue_state in TARGET_SERVICE_STATES
        mode = health.vehicle_mode.upper()
        if mode in NATIVE_FAILSAFE_MODES and health.rescue_state not in {"RETURNING", "LANDING", "COMPLETED"}:
            return self._decision(
                SafetySeverity.EMERGENCY, "NATIVE_AUTOPILOT_FAILSAFE", "autopilot",
                SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE, FailsafeOwnership.AUTOPILOT, health,
                previous_mode=health.previous_vehicle_mode, new_mode=mode,
                statustext=health.native_failsafe_evidence,
            )
        if health.state("geofence") is HealthState.FAILED:
            return self._decision(
                SafetySeverity.EMERGENCY, "GEOFENCE_BREACH", "autopilot",
                SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE, FailsafeOwnership.AUTOPILOT, health,
            )
        if health.state("battery") is HealthState.FAILED:
            return self._decision(
                SafetySeverity.EMERGENCY, "CRITICAL_BATTERY", "autopilot",
                SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE, FailsafeOwnership.AUTOPILOT, health,
            )
        if health.state("ekf") is HealthState.FAILED:
            return self._decision(
                SafetySeverity.CRITICAL, "EKF_FAILED", "autopilot",
                SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE, FailsafeOwnership.AUTOPILOT, health,
            )
        if health.state("mavlink") in {HealthState.FAILED, HealthState.STALE}:
            action = SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE if in_service else SafetyAction.CONTINUE
            return self._decision(
                SafetySeverity.CRITICAL if in_service else SafetySeverity.WARNING,
                "CONTROL_LINK_LOST" if in_service else "MAVLINK_LOST_DURING_AUTO",
                "mavlink", action,
                FailsafeOwnership.AUTOPILOT if in_service else FailsafeOwnership.AUTONOMY,
                health, drop_permitted=False,
            )
        if health.guided_command_age_s is not None and in_service and health.guided_command_age_s > 1.0:
            return self._decision(
                SafetySeverity.CRITICAL, "GUIDED_COMMAND_WATCHDOG", "autonomy",
                SafetyAction.REQUEST_RTL, FailsafeOwnership.AUTONOMY, health,
                command_age_s=health.guided_command_age_s, drop_permitted=False,
            )
        if in_service and health.state("gps") in {HealthState.FAILED, HealthState.STALE}:
            return self._decision(
                SafetySeverity.CRITICAL, "GPS_INVALID_DURING_TARGET_SERVICE", "pose",
                SafetyAction.ABORT_TARGET_SERVICE, FailsafeOwnership.AUTONOMY, health,
                drop_permitted=False,
            )
        if in_service and health.state("vision") in {HealthState.FAILED, HealthState.STALE}:
            return self._decision(
                SafetySeverity.CRITICAL, "VISION_REQUIRED_FOR_TARGET_SERVICE", "vision",
                SafetyAction.ABORT_TARGET_SERVICE, FailsafeOwnership.AUTONOMY, health,
                drop_permitted=False,
            )
        if in_service and health.state("camera") in {HealthState.FAILED, HealthState.STALE}:
            return self._decision(
                SafetySeverity.CRITICAL, "CAMERA_STALE_DURING_TARGET_SERVICE", "camera",
                SafetyAction.ABORT_TARGET_SERVICE, FailsafeOwnership.AUTONOMY, health,
                drop_permitted=False,
            )
        if in_service and health.state("detector") in {HealthState.FAILED, HealthState.STALE}:
            return self._decision(
                SafetySeverity.CRITICAL, "DETECTOR_STALE_DURING_TARGET_SERVICE", "detector",
                SafetyAction.ABORT_TARGET_SERVICE, FailsafeOwnership.AUTONOMY, health,
                drop_permitted=False,
            )
        if health.mission_deadline_s - health.mission_elapsed_s <= 60:
            return self._decision(
                SafetySeverity.WARNING, "MISSION_DEADLINE_RESERVE", "mission",
                SafetyAction.ABORT_TARGET_SERVICE if in_service else SafetyAction.DEFER_TARGET,
                FailsafeOwnership.AUTONOMY, health,
            )
        if health.state("battery") is HealthState.DEGRADED:
            return self._decision(
                SafetySeverity.WARNING, "LOW_BATTERY", "battery",
                SafetyAction.ABORT_TARGET_SERVICE if in_service else SafetyAction.DEFER_TARGET,
                FailsafeOwnership.AUTONOMY, health,
            )
        if health.boundary_distance_m is not None and health.boundary_distance_m < 0:
            return self._decision(
                SafetySeverity.CRITICAL, "OPERATIONAL_BOUNDARY_BREACH", "autonomy",
                SafetyAction.REQUEST_RTL, FailsafeOwnership.AUTONOMY, health,
            )
        if health.boundary_distance_m is not None and health.boundary_distance_m < 2:
            return self._decision(
                SafetySeverity.WARNING, "BOUNDARY_MARGIN_LOW", "autonomy",
                SafetyAction.CONTINUE, FailsafeOwnership.AUTONOMY, health,
                boundary_distance_m=health.boundary_distance_m,
            )
        degraded = [name for name, item in health.components.items() if item.effective_state(health.timestamp) in {HealthState.DEGRADED, HealthState.FAILED, HealthState.STALE}]
        if degraded:
            return self._decision(
                SafetySeverity.DEGRADED, "NONCRITICAL_COMPONENT_DEGRADED", "system",
                SafetyAction.CONTINUE, FailsafeOwnership.AUTONOMY, health, components=degraded,
            )
        return self._decision(
            SafetySeverity.INFO, "ALL_REQUIRED_HEALTH_READY", "system",
            SafetyAction.CONTINUE, FailsafeOwnership.AUTONOMY, health,
        )

    @staticmethod
    def aggregate(health: SystemHealthSnapshot) -> AggregateHealth:
        states = {name: item.effective_state(health.timestamp) for name, item in health.components.items() if item.required}
        if health.vehicle_mode.upper() in NATIVE_FAILSAFE_MODES or any(
            states.get(name) is HealthState.FAILED for name in ("battery", "ekf", "geofence")
        ):
            return AggregateHealth.EMERGENCY
        if any(state in {HealthState.FAILED, HealthState.STALE, HealthState.UNKNOWN} for state in states.values()):
            return AggregateHealth.NOT_READY
        if any(state is HealthState.DEGRADED for state in states.values()):
            return AggregateHealth.DEGRADED
        return AggregateHealth.READY

    @staticmethod
    def _decision(
        severity: SafetySeverity, reason: str, source: str, action: SafetyAction,
        owner: FailsafeOwnership, health: SystemHealthSnapshot, **evidence: object,
    ) -> SafetyDecision:
        return SafetyDecision(
            severity=severity, reason=reason, source=source,
            recommended_action=action, authoritative_layer=owner,
            evidence={"rescue_state": health.rescue_state, "vehicle_mode": health.vehicle_mode, **evidence},
        )
