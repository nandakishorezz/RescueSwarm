from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.planning.coverage_planner import CoveragePlanner, PlanningError
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryError, BoundaryService
from ground.backend.app.services.planning_service import coverage_plan_fingerprint
from ground.backend.app.services.telemetry_service import RealtimeEnvelope
from ground.backend.app.services.websocket_manager import WebSocketManager
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from rescue_core.qualification import (
    CompetitionPreflightValidator,
    FleetDeadlineManager,
    MissionObjectiveTracker,
    MissionOutcome,
    SetupTimeProfiler,
    load_competition_profile,
)
from rescue_core.qualification.audit import runtime_socket_audit, secret_audit, source_manifest
from scripts.run_stage10s_qualification import scenario_result, targets

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "fixtures" / "stage10s"


def profile():
    return load_competition_profile(ROOT / "config" / "competition_profile.yaml")


def coverage_request(edge_margin_m: float = 2):
    return CoverageRequest(
        search_altitude_m=20, lane_spacing_m=20, effective_swath_m=20,
        edge_margin_m=edge_margin_m, search_speed_m_s=8, min_planned_coverage_percent=98,
    )


@pytest.mark.stage10s
def test_profile_centralizes_rules_and_preserves_h1():
    value = profile()
    assert value.competition.max_search_area_hectares == 10
    assert value.competition.max_survivors == 10
    assert value.competition.minimum_drones == 2
    assert value.hardware_gate_h1 == "PENDING"
    assert value.physical_requirements.evidence_status == "PENDING_H1"
    assert not value.competition.external_network_allowed


@pytest.mark.stage10s
def test_fixture_library_is_valid_and_near_limit_is_below_ten_hectares():
    service = BoundaryService(10)
    names = {item.name for item in FIXTURES.glob("*.geojson")}
    assert names == {
        "small_easy.geojson", "concave_medium.geojson", "long_narrow.geojson",
        "irregular_large.geojson", "near_10_hectare.geojson",
    }
    boundaries = {name: service.import_boundary(name, (FIXTURES / name).read_bytes()) for name in names}
    assert 9.9 < boundaries["near_10_hectare.geojson"].area_hectares <= 10
    assert all(not item.warnings for item in boundaries.values())


@pytest.mark.stage10s
def test_adversarial_boundaries_fail_closed_before_planning():
    service = BoundaryService(10)
    invalid = [
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [0, 0], [0, 0], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[78, 17], [78.001, 17], [78.001, 17], [78, 17.001], [78, 17]]]},
        {"type": "Polygon", "coordinates": [[[181, 0], [181, 1], [182, 1], [181, 0]]]},
    ]
    for index, value in enumerate(invalid):
        with pytest.raises(BoundaryError, match="Boundary|GeoJSON"):
            service.import_boundary(f"invalid-{index}.geojson", json.dumps(value).encode())
    for payload in (b"", b"[]", b"not-json", b'{"type":"FeatureCollection","features":[]}'):
        with pytest.raises(BoundaryError):
            service.import_boundary("fuzz.geojson", payload)


@pytest.mark.stage10s
def test_over_limit_is_rejected_by_competition_preflight():
    small = BoundaryService(10).import_boundary("small.geojson", (FIXTURES / "small_easy.geojson").read_bytes())
    checks = CompetitionPreflightValidator(profile()).evaluate(
        area_hectares=10.01, fleet_size=2, vehicle_ids=["drone_01", "drone_02"],
        system_ids=[1, 2], missions_built=2, payload_inventory_known=True,
        offline_pass=True, engineering_enabled=False, required_assets_present=True,
    )
    assert small.area_hectares < 10
    assert next(item for item in checks if item.name == "mission_area").status == "FAIL"
    assert not CompetitionPreflightValidator.software_ready(checks)


@pytest.mark.stage10s
def test_edge_margin_collapse_is_rejected():
    boundary = BoundaryService(10).import_boundary("narrow.geojson", (FIXTURES / "long_narrow.geojson").read_bytes())
    with pytest.raises(PlanningError, match="collapses"):
        CoveragePlanner().plan("collapse", boundary, coverage_request(edge_margin_m=30))


@pytest.mark.stage10s
def test_near_ten_hectare_planning_and_fingerprints_are_deterministic():
    boundary = BoundaryService(10).import_boundary("near.geojson", (FIXTURES / "near_10_hectare.geojson").read_bytes())
    values = []
    for _ in range(2):
        plan = CoveragePlanner().plan("deterministic", boundary, coverage_request())
        plan.plan_fingerprint = coverage_plan_fingerprint(plan)
        fleet = FleetCoveragePartitioner().partition(plan, ["drone_01", "drone_02"])
        missions = [ArduCopterMissionBuilder().build(item).fingerprint for item in fleet.vehicle_plans.values()]
        values.append((plan.plan_fingerprint, fleet.partition_fingerprint, missions))
    assert values[0] == values[1]
    assert len(set(values[0][2])) == 2


@pytest.mark.stage10s
def test_three_vehicle_scale_partition_remains_complete():
    boundary = BoundaryService(10).import_boundary("near.geojson", (FIXTURES / "near_10_hectare.geojson").read_bytes())
    plan = CoveragePlanner().plan("three", boundary, coverage_request())
    fleet = FleetCoveragePartitioner().partition(plan, ["drone_01", "drone_02", "drone_03"])
    assigned = [segment for assignment in fleet.assignments for segment in assignment.segment_ids]
    assert fleet.fleet_size == 3
    assert len(assigned) == len(set(assigned)) == len(plan.segments)


@pytest.mark.stage10s
def test_ten_survivor_registry_merges_cross_vehicle_duplicates_only():
    registry, merges = targets(10)
    assert len(registry.all()) == 10
    assert merges == 10
    assert all(item.observing_vehicle_ids == ["drone_01", "drone_02"] for item in registry.all())


@pytest.mark.stage10s
def test_objective_tracker_reports_capacity_and_deadline_honestly():
    capacity = scenario_result("capacity", 10, 3, 99)
    deadline = scenario_result("deadline", 4, 5, 96, deadline=True)
    assert capacity["verified_deliveries"] == 6
    assert capacity["outcome"] == MissionOutcome.CAPACITY_LIMITED
    assert capacity["inventory_violations"] == 0
    assert deadline["outcome"] == MissionOutcome.DEADLINE_LIMITED
    assert deadline["verified_deliveries"] == 0


@pytest.mark.stage10s
def test_mission_landing_alone_does_not_imply_success():
    result = MissionObjectiveTracker().classify(
        coverage_required_percent=98, coverage_actual_percent=50,
        targets_discovered=2, targets_confirmed=2, targets_serviceable=2,
        targets_delivered=0, payload_starting=2, payload_remaining=2,
        vehicles_available=0, vehicles_terminal_safe=2, vehicles_deployed=2,
    )
    assert result.outcome == MissionOutcome.VEHICLE_LIMITED


@pytest.mark.stage10s
def test_fleet_deadline_manager_is_conservative_and_auditable():
    manager = FleetDeadlineManager(profile())
    assert manager.decide(elapsed_s=100, remaining_search_s=100, pending_service_count=1).action == "CONTINUE"
    defer = manager.decide(elapsed_s=1450, remaining_search_s=100, pending_service_count=1)
    recall = manager.decide(elapsed_s=1600, remaining_search_s=10, pending_service_count=0)
    abort = manager.decide(elapsed_s=1600, remaining_search_s=10, pending_service_count=1, active_service=True)
    assert defer.action == "DEFER_DELIVERY"
    assert recall.action == "BEGIN_FLEET_RECALL"
    assert abort.action == "ABORT_TARGET_SERVICE_AND_RECALL"
    assert defer.evidence["physical_timing_claim"] is False


@pytest.mark.stage10s
def test_setup_profiler_is_bounded_and_labels_physical_time_unknown():
    result = SetupTimeProfiler().run(2, {"noop": lambda: None})
    assert result["timings_ms"]["noop"]["p95"] >= 0
    assert result["peak_traced_allocation_bytes"] >= 0
    assert result["physical_setup_five_minute_compliance"] == "NOT_MEASURED"


class _FastSocket:
    def __init__(self):
        self.sent = 0

    async def send_json(self, payload):
        self.sent += 1


class _StalledSocket:
    async def send_json(self, payload):
        await asyncio.sleep(1)


@pytest.mark.stage10s
def test_websocket_slow_client_expires_without_blocking_fast_client():
    async def exercise():
        manager = WebSocketManager(send_timeout_s=.01)
        fast, slow = _FastSocket(), _StalledSocket()
        manager.connections["mission"] = {fast, slow}
        envelope = RealtimeEnvelope(mission_id="mission", vehicle_id=None, type="system.snapshot", sequence=1, payload={})
        await manager.broadcast(envelope)
        assert fast.sent == 1
        assert manager.connections["mission"] == {fast}
    asyncio.run(exercise())


@pytest.mark.stage10s
def test_release_audits_are_local_redacted_and_deterministic(tmp_path):
    first, second = source_manifest(ROOT), source_manifest(ROOT)
    assert first["aggregate_sha256"] == second["aggregate_sha256"]
    assert first["rescueswarm_commit"] == "UNKNOWN"
    assert secret_audit(ROOT)["passed"]
    sockets = runtime_socket_audit()
    assert sockets["status"] in {"PASS", "NOT_TESTED"}
