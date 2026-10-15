"""Scale, deadline, setup, and preflight qualification without flight authority."""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

import psutil
import yaml

from rescue_core.resilience.models import OperatorAction

from .models import (
    CompetitionScaleProfile,
    DeadlineDecision,
    MissionObjectiveSummary,
    MissionOutcome,
    PreflightCheck,
    PreflightStatus,
)


def load_competition_profile(path: Path) -> CompetitionScaleProfile:
    return CompetitionScaleProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class MissionObjectiveTracker:
    """Classifies outcome from independent mission objectives, never from landing alone."""

    def classify(
        self, *, coverage_required_percent: float, coverage_actual_percent: float,
        targets_discovered: int, targets_confirmed: int, targets_serviceable: int,
        targets_delivered: int, payload_starting: int, payload_remaining: int,
        vehicles_available: int, vehicles_terminal_safe: int, vehicles_deployed: int,
        deadline_reached: bool = False, mission_aborted: bool = False,
        critical_failure: bool = False,
    ) -> MissionObjectiveSummary:
        unresolved = max(0, targets_serviceable - targets_delivered)
        reasons: list[str] = []
        all_safe = vehicles_terminal_safe == vehicles_deployed
        coverage_ok = coverage_actual_percent >= coverage_required_percent
        if not all_safe:
            outcome = MissionOutcome.FAILED_SAFE
            reasons.append("Not every deployed vehicle reached a documented safe terminal state")
        elif critical_failure:
            outcome = MissionOutcome.FAILED_SAFE
            reasons.append("A critical failure prevented safe objective completion")
        elif mission_aborted:
            outcome = MissionOutcome.ABORTED_SAFE
            reasons.append("Mission was explicitly aborted and vehicles terminated safely")
        elif deadline_reached and (not coverage_ok or unresolved):
            outcome = MissionOutcome.DEADLINE_LIMITED
            reasons.append("Deadline reserve prevented remaining objectives")
        elif unresolved and payload_remaining == 0:
            outcome = MissionOutcome.CAPACITY_LIMITED
            reasons.append("Confirmed serviceable targets exceed remaining payload capacity")
        elif vehicles_available < 1 and (not coverage_ok or unresolved):
            outcome = MissionOutcome.VEHICLE_LIMITED
            reasons.append("No eligible vehicle remains for unresolved work")
        elif coverage_ok and unresolved == 0:
            outcome = MissionOutcome.SUCCESS
            reasons.append("Coverage, target outcomes, and safe terminal-state objectives are resolved")
        else:
            outcome = MissionOutcome.PARTIAL_SUCCESS
            reasons.append("Some objectives are resolved but coverage or target service remains incomplete")
        return MissionObjectiveSummary(
            coverage_required_percent=coverage_required_percent,
            coverage_actual_percent=coverage_actual_percent,
            targets_discovered=targets_discovered,
            targets_confirmed=targets_confirmed,
            targets_serviceable=targets_serviceable,
            targets_delivered=targets_delivered,
            targets_unresolved=unresolved,
            payload_starting=payload_starting,
            payload_remaining=payload_remaining,
            vehicles_available=vehicles_available,
            vehicles_terminal_safe=vehicles_terminal_safe,
            vehicles_deployed=vehicles_deployed,
            deadline_reached=deadline_reached,
            mission_aborted=mission_aborted,
            critical_failure=critical_failure,
            outcome=outcome,
            reasons=reasons,
        )


class FleetDeadlineManager:
    """Conservative fleet timing decision; it does not command vehicles."""

    def __init__(self, profile: CompetitionScaleProfile):
        self.profile = profile

    def decide(
        self, *, elapsed_s: float, remaining_search_s: float,
        pending_service_count: int, estimated_service_s: float | None = None,
        active_service: bool = False,
    ) -> DeadlineDecision:
        q = self.profile.qualification
        service = (estimated_service_s or q.target_service_estimate_s) if pending_service_count else 0.0
        remaining = max(0.0, self.profile.competition.max_mission_flight_time_s - elapsed_s)
        required = (
            remaining_search_s + service + q.rtl_reserve_s + q.landing_queue_reserve_s
        ) * q.deadline_safety_factor
        hard_return = (q.rtl_reserve_s + q.landing_queue_reserve_s) * q.deadline_safety_factor
        if remaining <= hard_return:
            action = "ABORT_TARGET_SERVICE_AND_RECALL" if active_service else "BEGIN_FLEET_RECALL"
        elif pending_service_count and remaining < required:
            action = "DEFER_DELIVERY"
        else:
            action = "CONTINUE"
        return DeadlineDecision(
            action=action,
            remaining_mission_s=remaining,
            estimated_remaining_search_s=remaining_search_s,
            estimated_target_service_s=service,
            rtl_reserve_s=q.rtl_reserve_s,
            landing_reserve_s=q.landing_queue_reserve_s,
            safety_factor=q.deadline_safety_factor,
            required_s=required,
            reason=f"remaining={remaining:.1f}s conservative_required={required:.1f}s action={action}",
            evidence={"simulation_time_semantics": True, "physical_timing_claim": False},
        )


class CompetitionPreflightValidator:
    def __init__(self, profile: CompetitionScaleProfile):
        self.profile = profile

    def evaluate(
        self, *, area_hectares: float, fleet_size: int, vehicle_ids: list[str],
        system_ids: list[int], missions_built: int, payload_inventory_known: bool,
        offline_pass: bool, engineering_enabled: bool, required_assets_present: bool,
    ) -> list[PreflightCheck]:
        rules = self.profile.competition
        allowed = {item.value for item in (
            OperatorAction.MISSION_START, OperatorAction.SAFETY_ABORT, OperatorAction.EMERGENCY_RECALL,
        )}
        checks = [
            PreflightCheck(name="mission_area", status=PreflightStatus.PASS if 0 < area_hectares <= rules.max_search_area_hectares else PreflightStatus.FAIL, reason=f"area={area_hectares:.6f}ha limit={rules.max_search_area_hectares}ha"),
            PreflightCheck(name="fleet_size", status=PreflightStatus.PASS if fleet_size >= rules.minimum_drones else PreflightStatus.FAIL, reason=f"fleet={fleet_size} minimum={rules.minimum_drones}"),
            PreflightCheck(name="vehicle_ids_unique", status=PreflightStatus.PASS if len(vehicle_ids) == len(set(vehicle_ids)) == fleet_size else PreflightStatus.FAIL, reason="Persistent vehicle IDs must be unique"),
            PreflightCheck(name="system_ids_unique", status=PreflightStatus.PASS if len(system_ids) == len(set(system_ids)) == fleet_size and all(value > 0 for value in system_ids) else PreflightStatus.FAIL, reason="Live MAVLink SYSIDs must be unique and non-broadcast"),
            PreflightCheck(name="missions_built", status=PreflightStatus.PASS if missions_built == fleet_size else PreflightStatus.FAIL, reason=f"built={missions_built} expected={fleet_size}"),
            PreflightCheck(name="payload_inventory", status=PreflightStatus.PASS if payload_inventory_known else PreflightStatus.FAIL, reason="Logical starting inventory must be explicit"),
            PreflightCheck(name="offline_guard", status=PreflightStatus.PASS if offline_pass else PreflightStatus.FAIL, reason="No external runtime dependency is permitted"),
            PreflightCheck(name="engineering_disabled", status=PreflightStatus.PASS if not engineering_enabled else PreflightStatus.FAIL, reason="Engineering APIs must be disabled in competition mode"),
            PreflightCheck(name="local_assets", status=PreflightStatus.PASS if required_assets_present else PreflightStatus.FAIL, reason="Required map/model/config assets must already be local"),
            PreflightCheck(name="operator_surface", status=PreflightStatus.PASS if set(rules.allowed_operator_actions) == allowed else PreflightStatus.FAIL, reason="Only START, ABORT, and RECALL are authorized"),
            PreflightCheck(name="hardware_gate_h1", status=PreflightStatus.PENDING, reason="Physical prototype evidence is unavailable", evidence_class="PHYSICAL"),
        ]
        return checks

    @staticmethod
    def software_ready(checks: list[PreflightCheck]) -> bool:
        return all(item.status is not PreflightStatus.FAIL for item in checks)


class SetupTimeProfiler:
    """Profiles pure software steps and resource use with uncached callables."""

    def run(self, iterations: int, steps: dict[str, Callable[[], Any]]) -> dict[str, Any]:
        if iterations < 2:
            raise ValueError("Setup qualification requires at least two iterations")
        timings: dict[str, list[float]] = {name: [] for name in steps}
        process = psutil.Process()
        rss_before = process.memory_info().rss
        tracemalloc.start()
        for _ in range(iterations):
            for name, operation in steps.items():
                started = time.perf_counter_ns()
                operation()
                timings[name].append((time.perf_counter_ns() - started) / 1e6)
        _, peak_traced = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = process.memory_info().rss
        return {
            "iterations": iterations,
            "timings_ms": {name: self._statistics(values) for name, values in timings.items()},
            "process_rss_before_bytes": rss_before,
            "process_rss_after_bytes": rss_after,
            "process_rss_growth_bytes": max(0, rss_after - rss_before),
            "peak_traced_allocation_bytes": peak_traced,
            "physical_setup_five_minute_compliance": "NOT_MEASURED",
        }

    @staticmethod
    def _statistics(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return {"p50": median(ordered), "p95": ordered[p95_index], "max": max(ordered)}
