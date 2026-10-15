"""Machine-readable deterministic Stage 8S fault campaign."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from .failures import FailureInjector
from .models import (
    ComponentHealth,
    FailureType,
    HealthState,
    ResilienceScenario,
    SafetyAction,
    ScenarioResult,
    SystemHealthSnapshot,
)
from .safety import SafetySupervisor


def load_scenarios(path: Path) -> list[ResilienceScenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("scenarios"), list):
        raise ValueError("Resilience scenario file requires a scenarios list")
    scenarios = [ResilienceScenario.model_validate(item) for item in raw["scenarios"]]
    identifiers = [item.scenario_id for item in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Resilience scenario IDs must be unique")
    return scenarios


class ResilienceScenarioRunner:
    def __init__(self):
        self.injector = FailureInjector(engineering_enabled=True, competition_enabled=False)
        self.supervisor = SafetySupervisor()

    @staticmethod
    def healthy_snapshot(phase: str) -> SystemHealthSnapshot:
        service_states = {
            "GUIDED_APPROACH": "GUIDED_APPROACH",
            "REACQUISITION": "REACQUIRING",
            "ALIGNMENT": "PRECISION_ALIGNING",
            "DELIVERY": "DELIVERING",
            "SEARCH_RESUME": "SEARCH_RESUMING",
        }
        state = service_states.get(phase, "SEARCHING" if phase not in {"PRE_MISSION", "TAKEOFF", "RTL", "LANDING"} else phase)
        mode = "GUIDED" if state in {"GUIDED_APPROACH", "REACQUIRING", "PRECISION_ALIGNING", "DELIVERING"} else "AUTO"
        components = {
            name: ComponentHealth(component=name, state=HealthState.READY, required=name not in {"database", "ground_link"})
            for name in ("autopilot", "mavlink", "gps", "ekf", "battery", "geofence", "vision", "camera", "detector", "payload", "database", "storage", "ground_link")
        }
        return SystemHealthSnapshot(
            mission_id="stage8s-campaign", rescue_state=state, vehicle_mode=mode,
            armed=phase not in {"PRE_MISSION", "LANDING"}, components=components,
        )

    def run(self, scenario: ResilienceScenario) -> ScenarioResult:
        initial = self.healthy_snapshot(scenario.phase)
        injection = self.injector.inject(
            scenario.failure, scenario.phase, **scenario.parameters,
        )
        injected_at = time.monotonic_ns()
        health = self.injector.apply(initial, injection)
        detected_at = time.monotonic_ns()
        decision = self._decision_for_nonhealth_failure(scenario, health) or self.supervisor.evaluate(health)
        decision_at = time.monotonic_ns()
        drop_prevented = not scenario.drop_permitted and decision.recommended_action in {
            SafetyAction.ABORT_TARGET_SERVICE,
            SafetyAction.REQUEST_RTL,
            SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE,
            SafetyAction.DEFER_TARGET,
            SafetyAction.CONTINUE,
        }
        final = self._final_state(decision.recommended_action, scenario)
        passed = (
            decision.recommended_action is scenario.expected_action
            and final in scenario.expected_final_states
            and (scenario.drop_permitted or drop_prevented)
        )
        violations = [] if passed else [
            f"expected action={scenario.expected_action}, actual={decision.recommended_action}, final={final}"
        ]
        return ScenarioResult(
            scenario_id=scenario.scenario_id, group=scenario.group,
            critical=scenario.critical, phase=scenario.phase,
            injection_method=scenario.injection_method, detected=True,
            decision=decision, drop_prevented=drop_prevented, final_state=final,
            recovered=final in {"SEARCHING", "COMPLETED"},
            detection_latency_ms=(detected_at - injected_at) / 1e6,
            decision_latency_ms=(decision_at - detected_at) / 1e6,
            safe_mode_latency_ms=0, violations=violations, passed=passed,
        )

    def _decision_for_nonhealth_failure(self, scenario: ResilienceScenario, health: SystemHealthSnapshot):
        failure = scenario.failure
        from .models import FailsafeOwnership, SafetyDecision, SafetySeverity
        if failure in {FailureType.GCS_BROWSER_DISCONNECT, FailureType.WEBSOCKET_DROP}:
            return SafetyDecision(
                severity=SafetySeverity.DEGRADED, reason=failure.value, source="ground_link",
                recommended_action=SafetyAction.CONTINUE,
                authoritative_layer=FailsafeOwnership.AUTONOMY,
                evidence={"browser_loss_is_not_mavlink_loss": True},
            )
        if failure in {FailureType.PAYLOAD_RELEASE_FAILURE, FailureType.PAYLOAD_VERIFICATION_FAILURE, FailureType.TARGET_LOSS}:
            return SafetyDecision(
                severity=SafetySeverity.WARNING, reason=failure.value, source="target_service",
                recommended_action=SafetyAction.ABORT_TARGET_SERVICE,
                authoritative_layer=FailsafeOwnership.AUTONOMY,
                evidence={"drop_permitted": False},
            )
        if failure in {FailureType.GROUND_BACKEND_RESTART, FailureType.ORCHESTRATOR_CRASH}:
            action = SafetyAction.REQUEST_RTL if health.vehicle_mode == "GUIDED" else SafetyAction.CONTINUE
            return SafetyDecision(
                severity=SafetySeverity.CRITICAL if action is SafetyAction.REQUEST_RTL else SafetySeverity.WARNING,
                reason=failure.value, source="process_supervision", recommended_action=action,
                authoritative_layer=FailsafeOwnership.AUTONOMY,
            )
        if failure is FailureType.EMERGENCY_RECALL:
            return SafetyDecision(
                severity=SafetySeverity.EMERGENCY, reason=failure.value, source="operator",
                recommended_action=SafetyAction.REQUEST_RTL,
                authoritative_layer=FailsafeOwnership.OPERATOR,
                evidence={"drop_permitted": False},
            )
        if failure in {FailureType.STALE_DETECTION, FailureType.GPS_POSITION_JUMP}:
            return SafetyDecision(
                severity=SafetySeverity.WARNING, reason=failure.value, source="geolocation",
                recommended_action=SafetyAction.ABORT_TARGET_SERVICE if health.vehicle_mode == "GUIDED" else SafetyAction.DEFER_TARGET,
                authoritative_layer=FailsafeOwnership.AUTONOMY,
            )
        return None

    @staticmethod
    def _final_state(action: SafetyAction, scenario: ResilienceScenario) -> str:
        if action is SafetyAction.CONTINUE:
            return "SEARCHING" if scenario.phase not in {"RTL", "LANDING"} else scenario.phase
        if action in {SafetyAction.ABORT_TARGET_SERVICE, SafetyAction.RESUME_SEARCH, SafetyAction.DEFER_TARGET}:
            return "SEARCHING"
        if action in {SafetyAction.REQUEST_RTL, SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE}:
            return "RETURNING"
        return "FAILED_SAFE"
