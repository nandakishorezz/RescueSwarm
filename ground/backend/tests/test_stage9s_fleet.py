from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from shapely.geometry import Polygon

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.fleet.allocation import AssignmentConflict, AssignmentLeaseManager, DeliveryAllocator
from ground.backend.app.fleet.coordinator import FleetMissionCoordinator
from ground.backend.app.fleet.models import (
    AssignmentLeaseState,
    FleetMassItem,
    FleetMassManifest,
    FleetPayloadCapacity,
    FleetState,
    FleetVehicle,
    VehicleFleetState,
)
from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.fleet.progress import FleetProgressTracker
from ground.backend.app.fleet.registry import DuplicateSystemIdError, SharedTargetRegistry, VehicleRegistry
from ground.backend.app.fleet.safety import AuthorizedFleetBroadcast, FleetCommandRouter, guarded_fleet_connections
from ground.backend.app.fleet.scheduling import CoordinatedLandingGate, FleetSeparationMonitor, LaunchLandingScheduler
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.multi_launcher import MultiSITLLauncher
from rescue_core.geolocation.models import FusedTargetEstimate, GeolocationQuality, TargetTrackState
from rescue_core.resilience.policies import CompetitionReadinessValidator


def boundary() -> Boundary:
    origin = GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500)
    frame = LocalCoordinateFrame(origin)
    points = [(0, 0), (80, 0), (80, 40), (0, 40)]
    return Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in points],
        source_format="stage9s_test", area_m2=3200, area_hectares=.32, origin=origin,
    )


def global_plan():
    return CoveragePlanner().plan("fleet-test", boundary(), CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))


def estimate(vehicle: str, source_id: str, east: float = 30, north: float = 20) -> FusedTargetEstimate:
    now = datetime.now(UTC)
    return FusedTargetEstimate(
        target_id=source_id, mission_id="fleet-test", state=TargetTrackState.CONFIRMED,
        east_m=east, north_m=north, latitude=17.4501, longitude=78.3801,
        uncertainty_m=2.5, detection_confidence=.8, observation_count=3,
        inlier_count=3, outlier_count=0, first_seen_at=now, last_seen_at=now,
        evidence_references=[f"{vehicle}:frame"], inlier_observation_ids=[f"{vehicle}:obs"],
        quality=GeolocationQuality.STANDARD_GNSS_LIMITED, simulated=True,
    )


def registry() -> VehicleRegistry:
    result = VehicleRegistry(3)
    for index in range(2):
        vehicle = FleetVehicle(
            vehicle_id=f"drone_{index + 1:02d}", system_id=index + 1,
            sitl_instance_id=index, connection=f"tcp:127.0.0.1:{5760 + 10 * index}",
            state=VehicleFleetState.SEARCHING, connected=True, payload_remaining=2,
            battery_percent=80, gps_healthy=True, ekf_healthy=True,
            east_m=index * 50, north_m=0,
        )
        result.register(vehicle)
        result.update(vehicle.vehicle_id, vehicle_sequence=1)
    return result


def test_partition_is_complete_connected_balanced_and_deterministic():
    plan = global_plan()
    partitioner = FleetCoveragePartitioner()
    first = partitioner.partition(plan, ["drone_01", "drone_02"])
    second = partitioner.partition(plan, ["drone_01", "drone_02"])
    assert first.partition_fingerprint == second.partition_fingerprint
    assigned = [segment for item in first.assignments for segment in item.segment_ids]
    assert len(assigned) == len(set(assigned)) == len(plan.segments)
    assert set(assigned) == {segment.segment_id for segment in plan.segments}
    assert first.metrics.disconnected_assignments == 0
    assert first.metrics.imbalance_percent < 60
    assert first.hardware_gate_h1 == "PENDING"


def test_partition_supports_three_vehicles_and_unique_fingerprints():
    plan = FleetCoveragePartitioner().partition(global_plan(), ["drone_01", "drone_02", "drone_03"])
    assert plan.fleet_size == 3
    assert len(plan.vehicle_plans) == 3
    assert len({item.plan_fingerprint for item in plan.vehicle_plans.values()}) == 3


def test_vehicle_registry_rejects_duplicate_sysid_and_tracks_independent_freshness():
    vehicles = registry()
    with pytest.raises(DuplicateSystemIdError):
        vehicles.register(FleetVehicle(
            vehicle_id="drone_03", system_id=2, sitl_instance_id=2,
            connection="tcp:127.0.0.1:5780",
        ))
    assert vehicles.is_fresh("drone_01")
    vehicles.get("drone_02").last_update_monotonic_ns = 1
    assert not vehicles.is_fresh("drone_02")
    assert vehicles.is_fresh("drone_01")


def test_shared_target_registry_cross_vehicle_deduplicates_and_preserves_evidence():
    targets = SharedTargetRegistry("fleet-test", association_radius_m=5)
    first = targets.ingest("drone_01", estimate("drone_01", "local-a", 30, 20))
    second = targets.ingest("drone_02", estimate("drone_02", "local-b", 31, 20.5))
    assert first.target_id == second.target_id
    assert len(targets.all()) == 1
    assert second.observing_vehicle_ids == ["drone_01", "drone_02"]
    assert targets.duplicate_merges == 1
    assert len(second.evidence_references) == 2


def test_distant_targets_remain_distinct():
    targets = SharedTargetRegistry("fleet-test", association_radius_m=4)
    targets.ingest("drone_01", estimate("drone_01", "a", 5, 5))
    targets.ingest("drone_02", estimate("drone_02", "b", 50, 30))
    assert len(targets.all()) == 2


def test_assignment_race_has_exactly_one_winner():
    leases = AssignmentLeaseManager()
    results, failures = [], []
    barrier = threading.Barrier(2)

    def acquire(vehicle_id: str):
        barrier.wait()
        try:
            results.append(leases.acquire("target", vehicle_id))
        except AssignmentConflict as exc:
            failures.append(exc)

    threads = [threading.Thread(target=acquire, args=(vehicle,)) for vehicle in ("drone_01", "drone_02")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 1
    assert len(failures) == 1
    assert leases.active_for_target("target").vehicle_id in {"drone_01", "drone_02"}


def test_payload_slot_has_at_most_one_active_assignment():
    leases = AssignmentLeaseManager()
    leases.acquire("target-a", "drone_01", payload_slot="drone_01:slot_01")
    with pytest.raises(AssignmentConflict):
        leases.acquire("target-b", "drone_01", payload_slot="drone_01:slot_01")


def test_allocator_uses_eligible_payload_vehicle_not_merely_closest():
    vehicles = registry()
    vehicles.get("drone_01").payload_remaining = 0
    targets = SharedTargetRegistry("fleet-test")
    target = targets.ingest("drone_01", estimate("drone_01", "a", 2, 0))
    lease = DeliveryAllocator(vehicles, AssignmentLeaseManager()).allocate(target)
    assert lease and lease.vehicle_id == "drone_02"


def test_simultaneous_targets_allocate_one_per_vehicle():
    vehicles = registry()
    targets = SharedTargetRegistry("fleet-test", association_radius_m=3)
    first = targets.ingest("drone_01", estimate("drone_01", "a", 1, 0))
    second = targets.ingest("drone_02", estimate("drone_02", "b", 49, 0))
    allocator = DeliveryAllocator(vehicles, AssignmentLeaseManager())
    first_lease = allocator.allocate(first)
    second_lease = allocator.allocate(second)
    assert first_lease and second_lease
    assert {first_lease.vehicle_id, second_lease.vehicle_id} == {"drone_01", "drone_02"}
    assert vehicles.get(first_lease.vehicle_id).current_target_id == first.target_id
    assert vehicles.get(second_lease.vehicle_id).current_target_id == second.target_id


@pytest.mark.parametrize("change", [
    {"native_failsafe": True},
    {"payload_remaining": 0},
    {"battery_percent": 10},
    {"gps_healthy": False},
    {"ekf_healthy": False},
])
def test_allocator_rejects_ineligible_vehicle(change):
    vehicles = registry()
    for vehicle in vehicles.all():
        for field, value in change.items():
            setattr(vehicle, field, value)
    target = SharedTargetRegistry("fleet-test").ingest("drone_01", estimate("drone_01", "a"))
    assert DeliveryAllocator(vehicles, AssignmentLeaseManager()).allocate(target) is None


def test_delivered_target_never_reallocated():
    vehicles = registry()
    target = SharedTargetRegistry("fleet-test").ingest("drone_01", estimate("drone_01", "a"))
    target.delivery_state = "DELIVERED"
    assert DeliveryAllocator(vehicles, AssignmentLeaseManager()).allocate(target) is None


def test_ambiguous_active_delivery_is_not_reassigned():
    leases = AssignmentLeaseManager()
    lease = leases.acquire("target", "drone_01")
    leases.transition(lease.assignment_id, AssignmentLeaseState.ACCEPTED)
    leases.transition(lease.assignment_id, AssignmentLeaseState.ACTIVE)
    leases.transition(lease.assignment_id, AssignmentLeaseState.AMBIGUOUS)
    with pytest.raises(AssignmentConflict):
        leases.transition(lease.assignment_id, AssignmentLeaseState.RELEASED)


def test_combined_coverage_is_union_not_sum_and_excludes_diversion():
    tracker = FleetProgressTracker(Polygon([(0, 0), (20, 0), (20, 10), (0, 10)]), 4)
    for x in range(21):
        tracker.add("drone_01", x, 4, "SEARCHING")
        tracker.add("drone_02", x, 4, "SEARCHING")
        tracker.add("drone_02", x, 8, "GUIDED_APPROACH")
    per = tracker.per_vehicle_coverage_percent()
    assert tracker.combined_coverage_percent() == pytest.approx(per["drone_01"], rel=1e-9)
    assert tracker.combined_coverage_percent() <= 100


def test_launch_and_landing_are_sequenced_by_evidence():
    vehicles = {item.vehicle_id: item for item in registry().all()}
    scheduler = LaunchLandingScheduler(["drone_01", "drone_02"], 3)
    assert scheduler.next_launch(vehicles) == "drone_01"
    assert scheduler.next_launch(vehicles) is None
    vehicles["drone_01"].altitude_m = 4
    assert scheduler.next_launch(vehicles) == "drone_02"
    scheduler.configure_landing(["drone_01", "drone_02"])
    assert scheduler.next_landing(vehicles) == "drone_01"
    assert scheduler.next_landing(vehicles) is None
    vehicles["drone_01"].state = VehicleFleetState.LANDED
    assert scheduler.next_landing(vehicles) == "drone_02"


def test_coordinated_landing_gate_grants_exactly_one_ordered_slot():
    gate = CoordinatedLandingGate(["drone_02", "drone_01"])
    assert not gate.try_acquire("drone_01")
    assert gate.try_acquire("drone_02")
    assert gate.try_acquire("drone_02")
    assert not gate.try_acquire("drone_01")
    gate.complete("drone_02")
    assert gate.try_acquire("drone_01")
    gate.complete("drone_01")
    assert gate.snapshot() == {
        "active": None,
        "queued": [],
        "completed": ["drone_02", "drone_01"],
    }


def test_separation_monitor_distinguishes_vertical_clearance():
    vehicles = registry().all()
    vehicles[0].east_m = vehicles[0].north_m = 0
    vehicles[1].east_m, vehicles[1].north_m = 2, 0
    vehicles[0].altitude_m, vehicles[1].altitude_m = 10, 10
    monitor = FleetSeparationMonitor(8, 3, 4)
    assert monitor.assess(vehicles)[0].state == "CRITICAL"
    vehicles[1].altitude_m = 15
    assert monitor.assess(vehicles)[0].state == "CLEAR"


def test_command_router_isolates_target_and_protects_broadcast():
    vehicles = registry()
    calls = []
    senders = {vehicle: (lambda vehicle_id: lambda **kwargs: calls.append((vehicle_id, kwargs)) or True)(vehicle)
               for vehicle in ("drone_01", "drone_02")}
    router = FleetCommandRouter(vehicles, senders)
    router.send_vehicle("drone_01", "HOLD")
    assert calls == [("drone_01", {"command": "HOLD", "target_system": 1, "target_component": 1})]
    with pytest.raises(PermissionError):
        router.send_vehicle("", "RTL")
    with pytest.raises(PermissionError):
        AuthorizedFleetBroadcast("AUTO")
    router.broadcast(AuthorizedFleetBroadcast("EMERGENCY_RECALL"), "RTL")
    assert {value[0] for value in calls[-2:]} == {"drone_01", "drone_02"}


@pytest.mark.parametrize("endpoint", ["/dev/serial0", "/dev/ttyACM0", "COM3", "tcp:8.8.8.8:5760"])
def test_fleet_command_connections_reject_physical_or_remote(endpoint):
    with pytest.raises(PermissionError):
        guarded_fleet_connections({"drone_01": endpoint, "drone_02": "tcp:127.0.0.1:5770"})


def test_multisitl_endpoint_derivation_is_unique_and_guarded():
    endpoints = MultiSITLLauncher(ardupilot_home="unused").endpoints(3)
    assert [item.connection for item in endpoints] == [
        "tcp:127.0.0.1:5760", "tcp:127.0.0.1:5770", "tcp:127.0.0.1:5780",
    ]
    assert [item.expected_system_id for item in endpoints] == [1, 2, 3]


def test_vehicle_failure_releases_pre_service_lease_and_reassigns_coverage():
    vehicles = registry()
    plan = FleetCoveragePartitioner().partition(global_plan(), ["drone_01", "drone_02"])
    targets = SharedTargetRegistry("fleet-test")
    target = targets.ingest("drone_01", estimate("drone_01", "a"))
    coordinator = FleetMissionCoordinator(plan, vehicles, targets)
    coordinator.state = FleetState.SEARCHING
    lease = coordinator.allocate_pending_targets()[0]
    failed_vehicle = lease["vehicle_id"]
    unfinished = coordinator.vehicle_failed(failed_vehicle)
    winner = coordinator.reassign_coverage(unfinished)
    assert winner and winner != failed_vehicle
    assert targets.get(target.target_id).delivery_state == "PENDING"
    replacement = coordinator.allocate_pending_targets()
    assert replacement and replacement[0]["vehicle_id"] == winner


def test_coordinator_recovery_snapshot_preserves_assignments_and_h1():
    vehicles = registry()
    plan = FleetCoveragePartitioner().partition(global_plan(), ["drone_01", "drone_02"])
    targets = SharedTargetRegistry("fleet-test")
    coordinator = FleetMissionCoordinator(plan, vehicles, targets)
    coordinator.prepare()
    snapshot = coordinator.snapshot()
    assert snapshot.fleet_state == FleetState.READY
    assert snapshot.fleet_plan["hardware_gate_h1"] == "PENDING"
    assert len(snapshot.sector_assignments) == 2


def test_stage9s_readiness_evidence_passes_fleet_but_overall_remains_not_ready():
    validator = CompetitionReadinessValidator()
    checks = validator.evaluate(include_stage9s=True)
    by_category = {item.category: item for item in checks}
    assert by_category["FLEET"].status == "PASS"
    assert by_category["HARDWARE"].status == "PENDING"
    assert validator.overall(checks) == "NOT_COMPETITION_READY"


def test_ten_target_registry_and_simulated_payload_capacity_scale():
    targets = SharedTargetRegistry("scale", association_radius_m=2)
    for index in range(10):
        targets.ingest(
            "drone_01" if index % 2 == 0 else "drone_02",
            estimate("scale", f"target-{index}", east=index * 10, north=index * 3),
        )
    assert len(targets.all()) == 10
    capacity = FleetPayloadCapacity(
        starting_by_vehicle={"drone_01": 5, "drone_02": 5},
        remaining_by_vehicle={"drone_01": 5, "drone_02": 5},
    )
    assert capacity.total_starting == capacity.total_remaining == 10
    assert capacity.evidence == "SIMULATED_PAYLOAD_CAPACITY_ONLY"


def test_mass_manifest_remains_unknown_without_physical_measurements():
    manifest = FleetMassManifest(vehicles=[FleetMassItem(vehicle_id="drone_01"), FleetMassItem(vehicle_id="drone_02")])
    assert manifest.combined_all_up_mass_kg is None
    assert manifest.evidence_status == "UNKNOWN_NOT_MEASURED"


def test_ten_hectare_class_boundary_plans_and_partitions_without_physical_claim():
    origin = GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500)
    frame = LocalCoordinateFrame(origin)
    points = [(0, 0), (400, 0), (400, 250), (0, 250)]
    large = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in points],
        source_format="ten_hectare_software_fixture", area_m2=100_000,
        area_hectares=10, origin=origin,
    )
    plan = CoveragePlanner().plan("ten-ha", large, CoverageRequest(
        search_altitude_m=20, lane_spacing_m=20, effective_swath_m=20,
        edge_margin_m=2, search_speed_m_s=8, min_planned_coverage_percent=98,
    ))
    fleet = FleetCoveragePartitioner().partition(plan, ["drone_01", "drone_02"])
    assert len(fleet.segments) == len(plan.segments)
    assert fleet.metrics.estimated_makespan_s > 0
    assert fleet.search_altitude_evidence == "SITL_ENGINEERING_DEFAULT"
