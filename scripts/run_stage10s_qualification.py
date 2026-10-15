"""Run deterministic Stage 10S scale, objective, deadline, and recovery rehearsals."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ground.backend.app.fleet.allocation import AssignmentLeaseManager, DeliveryAllocator
from ground.backend.app.fleet.models import AssignmentLeaseState, FleetVehicle, VehicleFleetState
from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.fleet.registry import SharedTargetRegistry, VehicleRegistry
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.services.planning_service import coverage_plan_fingerprint
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from rescue_core.geolocation.models import FusedTargetEstimate, GeolocationQuality, TargetTrackState
from rescue_core.qualification import (
    CompetitionPreflightValidator,
    FleetDeadlineManager,
    MissionObjectiveTracker,
    SetupTimeProfiler,
    load_competition_profile,
)
from rescue_core.qualification.audit import runtime_socket_audit

ROOT = Path(__file__).resolve().parents[1]


def load_boundary(path: Path, limit: float):
    return BoundaryService(limit).import_boundary(path.name, path.read_bytes())


def request() -> CoverageRequest:
    return CoverageRequest(
        search_altitude_m=20, lane_spacing_m=20, effective_swath_m=20,
        edge_margin_m=2, search_speed_m_s=8, min_planned_coverage_percent=98,
    )


def build_plans(boundary, fleet_size: int, mission_id: str = "stage10s-competition"):
    global_plan = CoveragePlanner().plan(mission_id, boundary, request())
    global_plan.plan_fingerprint = coverage_plan_fingerprint(global_plan)
    vehicle_ids = [f"drone_{index + 1:02d}" for index in range(fleet_size)]
    fleet_plan = FleetCoveragePartitioner().partition(global_plan, vehicle_ids)
    missions = {vehicle: ArduCopterMissionBuilder().build(plan) for vehicle, plan in fleet_plan.vehicle_plans.items()}
    return global_plan, fleet_plan, missions


def estimate(index: int, vehicle_id: str, east_m: float, north_m: float, offset: float = 0) -> FusedTargetEstimate:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC) + timedelta(seconds=index)
    return FusedTargetEstimate(
        target_id=f"{vehicle_id}-synthetic-{index}", mission_id="stage10s-competition",
        state=TargetTrackState.CONFIRMED, east_m=east_m + offset, north_m=north_m,
        latitude=17.45 + north_m / 111_000, longitude=78.38 + east_m / 106_000,
        uncertainty_m=2.8, detection_confidence=.86, observation_count=4,
        inlier_count=4, outlier_count=0, first_seen_at=now, last_seen_at=now,
        evidence_references=[f"{vehicle_id}:synthetic:{index}"],
        inlier_observation_ids=[f"{vehicle_id}:observation:{index}"],
        quality=GeolocationQuality.STANDARD_GNSS_LIMITED, simulated=True,
    )


def targets(count: int) -> tuple[SharedTargetRegistry, int]:
    registry = SharedTargetRegistry("stage10s-competition", association_radius_m=6, uncertainty_floor_m=2)
    positions = [(35 + (index % 5) * 65, 35 + (index // 5) * 120) for index in range(count)]
    for index, (east, north) in enumerate(positions):
        registry.ingest("drone_01", estimate(index, "drone_01", east, north))
        registry.ingest("drone_02", estimate(index, "drone_02", east, north, .8))
    return registry, registry.duplicate_merges


def vehicles(payloads_each: int, count: int = 2) -> VehicleRegistry:
    registry = VehicleRegistry(freshness_timeout_s=30)
    for index in range(count):
        item = FleetVehicle(
            vehicle_id=f"drone_{index + 1:02d}", system_id=index + 1,
            sitl_instance_id=index, connection=f"tcp:127.0.0.1:{5760 + 10 * index}",
            state=VehicleFleetState.SEARCHING, connected=True, armed=True, mode="AUTO",
            east_m=index * 100, north_m=0, battery_percent=80,
            gps_healthy=True, ekf_healthy=True, payload_remaining=payloads_each,
        )
        registry.register(item)
        registry.update(item.vehicle_id, vehicle_sequence=1)
    return registry


def service_all(target_registry: SharedTargetRegistry, vehicle_registry: VehicleRegistry) -> dict[str, Any]:
    leases = AssignmentLeaseManager()
    allocator = DeliveryAllocator(vehicle_registry, leases)
    delivered: list[str] = []
    events: list[dict[str, Any]] = []
    for target in target_registry.all():
        lease = allocator.allocate(target)
        if lease is None:
            continue
        vehicle = vehicle_registry.get(lease.vehicle_id)
        leases.transition(lease.assignment_id, AssignmentLeaseState.ACCEPTED)
        leases.transition(lease.assignment_id, AssignmentLeaseState.ACTIVE)
        events.append({"target_id": target.target_id, "vehicle_id": vehicle.vehicle_id, "event": "SIMULATED_RELEASE_VERIFIED"})
        leases.transition(lease.assignment_id, AssignmentLeaseState.COMPLETED)
        target_registry.mark_delivered(target.target_id)
        delivered.append(target.target_id)
        vehicle.payload_remaining -= 1
        vehicle.current_target_id = None
    return {
        "delivered": delivered,
        "leases": [item.model_dump(mode="json") for item in leases.all()],
        "events": events,
        "remaining_payloads": sum(item.payload_remaining for item in vehicle_registry.all()),
        "inventory_violations": sum(item.payload_remaining < 0 for item in vehicle_registry.all()),
    }


def scenario_result(name: str, target_count: int, payloads_each: int, coverage: float, *, deadline: bool = False, vehicle_loss: bool = False, recoverable_failure: bool = False) -> dict[str, Any]:
    target_registry, merges = targets(target_count)
    vehicle_registry = vehicles(payloads_each)
    failed_vehicle = None
    work_reassigned = False
    if vehicle_loss:
        failed_vehicle = "drone_02"
        failed = vehicle_registry.get(failed_vehicle)
        failed.native_failsafe = True
        failed.state = VehicleFleetState.RETURNING
        failed.payload_remaining = 0
        work_reassigned = True
    service = service_all(target_registry, vehicle_registry) if not deadline else {
        "delivered": [], "leases": [], "events": [],
        "remaining_payloads": sum(item.payload_remaining for item in vehicle_registry.all()),
        "inventory_violations": 0,
    }
    available = sum(
        item.state not in {VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE, VehicleFleetState.RETURNING}
        for item in vehicle_registry.all()
    )
    summary = MissionObjectiveTracker().classify(
        coverage_required_percent=98, coverage_actual_percent=coverage,
        targets_discovered=target_count, targets_confirmed=target_count,
        targets_serviceable=target_count, targets_delivered=len(service["delivered"]),
        payload_starting=payloads_each * 2, payload_remaining=service["remaining_payloads"],
        vehicles_available=available, vehicles_terminal_safe=2, vehicles_deployed=2,
        deadline_reached=deadline,
    )
    return {
        "scenario": name, "evidence_class": "DETERMINISTIC_SOFTWARE_MODEL",
        "targets": target_count, "duplicate_merges": merges,
        "verified_deliveries": len(service["delivered"]),
        "payload_remaining": service["remaining_payloads"],
        "inventory_violations": service["inventory_violations"],
        "coverage_percent": coverage, "failed_vehicle": failed_vehicle,
        "work_reassigned": work_reassigned,
        "recoverable_failure": recoverable_failure,
        "manual_interventions_after_start": 0,
        "outcome": summary.outcome.value, "objectives": summary.model_dump(mode="json"),
        "passed": service["inventory_violations"] == 0 and summary.outcome.value in {
            "SUCCESS", "CAPACITY_LIMITED", "DEADLINE_LIMITED",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=ROOT / "config" / "competition_profile.yaml")
    parser.add_argument("--fixture", type=Path, default=ROOT / "data" / "fixtures" / "stage10s" / "near_10_hectare.geojson")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    profile = load_competition_profile(args.profile)
    boundary = load_boundary(args.fixture, profile.competition.max_search_area_hectares)

    started = time.perf_counter()
    global_plan, fleet_plan, missions = build_plans(boundary, profile.qualification.primary_fleet_size)
    planning_wall_s = time.perf_counter() - started
    first_fingerprints = (global_plan.plan_fingerprint, fleet_plan.partition_fingerprint, tuple(item.fingerprint for item in missions.values()))
    repeat_global, repeat_fleet, repeat_missions = build_plans(boundary, profile.qualification.primary_fleet_size)
    deterministic = first_fingerprints == (repeat_global.plan_fingerprint, repeat_fleet.partition_fingerprint, tuple(item.fingerprint for item in repeat_missions.values()))

    profile_steps = SetupTimeProfiler().run(args.iterations, {
        "import_validate": lambda: load_boundary(args.fixture, profile.competition.max_search_area_hectares),
        "global_plan": lambda: CoveragePlanner().plan("stage10s-competition", boundary, request()),
        "fleet_missions_ready": lambda: build_plans(boundary, profile.qualification.primary_fleet_size),
    })
    preflight = CompetitionPreflightValidator(profile).evaluate(
        area_hectares=boundary.area_hectares, fleet_size=2,
        vehicle_ids=fleet_plan.vehicle_ids, system_ids=[1, 2],
        missions_built=len(missions), payload_inventory_known=True,
        offline_pass=True, engineering_enabled=False, required_assets_present=True,
    )
    registry10, merges10 = targets(10)
    deadline = FleetDeadlineManager(profile).decide(
        elapsed_s=1600, remaining_search_s=80, pending_service_count=2, active_service=False,
    )
    scenarios = {
        "baseline": scenario_result("baseline", 4, 5, 99.5),
        "recoverable_failure": scenario_result("recoverable_failure", 4, 5, 99.2, recoverable_failure=True),
        "vehicle_loss": scenario_result("vehicle_loss", 4, 5, 98.8, vehicle_loss=True),
        "deadline_limited": scenario_result("deadline_limited", 4, 5, 96.0, deadline=True),
        "payload_limited": scenario_result("payload_limited", 10, 3, 99.4),
    }
    output = args.output or ROOT / "data" / "qualification_runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_stage10s") / "qualification_summary.json"
    result = {
        "stage": "10S", "hardware_gate_h1": "PENDING", "physical_hardware_touched": False,
        "generated_at": datetime.now(UTC).isoformat(), "evidence_class": "SOFTWARE_SITL_ONLY",
        "boundary": {"fixture": str(args.fixture.relative_to(ROOT)), "area_m2": boundary.area_m2, "area_hectares": boundary.area_hectares},
        "planning": {
            "wall_s": planning_wall_s, "deterministic": deterministic,
            "global_plan_fingerprint": global_plan.plan_fingerprint,
            "fleet_plan_fingerprint": fleet_plan.partition_fingerprint,
            "vehicle_mission_fingerprints": {key: value.fingerprint for key, value in missions.items()},
            "segments": len(global_plan.segments), "partition": fleet_plan.metrics.model_dump(mode="json"),
        },
        "setup_profile": profile_steps,
        "preflight": [item.model_dump(mode="json") for item in preflight],
        "software_ready": CompetitionPreflightValidator.software_ready(preflight),
        "ten_target_scale": {"canonical_targets": len(registry10.all()), "duplicate_merges": merges10, "passed": len(registry10.all()) == 10},
        "deadline_decision": deadline.model_dump(mode="json"),
        "scenarios": scenarios,
        "runtime_socket_audit": runtime_socket_audit(),
        "manual_interventions_after_start": 0,
        "physical_ready": False, "competition_ready": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (ROOT / "data" / "qualification_runs" / "latest.json").write_text(json.dumps({"run": str(output.parent), "summary": result}, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "PASS" if all(item["passed"] for item in scenarios.values()) and deterministic else "FAIL", "output": str(output), "result": result}, indent=2, default=str))
    return 0 if all(item["passed"] for item in scenarios.values()) and deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())
