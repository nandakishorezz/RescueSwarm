"""Phase 2C dispatch policy tests; observations are deterministic, not live claims."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ground.backend.app.api.multidrone import router
from ground.backend.app.multidrone.contracts import (
    AssignmentState, DroneInstance, DroneRole, FleetConfiguration, FleetSession,
    PrioritySource, ServicePriority, SurvivorObservation,
)
from ground.backend.app.multidrone.coordinator import ExclusiveServiceCoordinator
from ground.backend.app.multidrone.registry import SharedSurvivorRegistry
from ground.backend.app.multidrone.runtime import MultiDroneRuntime
from ground.backend.app.multidrone.state_machine import FleetState, FleetStateMachine


def setup_fleet(servicers=1, available=True):
    drones = [DroneInstance(vehicle_id="drone_01", role=DroneRole.SEARCHER,
                            system_id=1, instance_id=0, spawn_north_m=0)]
    drones.extend(DroneInstance(vehicle_id=f"drone_{i:02d}", role=DroneRole.SERVICER,
                                system_id=i, instance_id=i - 1, spawn_north_m=2 * (i - 1))
                  for i in range(2, servicers + 2))
    config = FleetConfiguration(mission_id="phase2c", drones=drones)
    session = FleetSession(session_id=uuid4(), mission_id="phase2c", run_id="r2c",
                           state="RUNNING", configuration=config, started_at=datetime.now(UTC))
    states = {d.vehicle_id: {
        "state": "SEARCHING", "connected": available, "telemetry_age_s": 0.1 if available else 9.0,
        "telemetry": {"latitude": 12.0, "longitude": 77.0 + d.instance_id * 0.0001,
                      "armed": True, "flight_mode": "AUTO"},
    } for d in drones[1:]}
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    coordinator = ExclusiveServiceCoordinator(session, config, registry,
                                               vehicles_state_supplier=lambda: states)
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed
    return session, states, registry, coordinator


def observe(session, registry, *, person, frame, east, vehicle="drone_01"):
    return registry.ingest(vehicle, SurvivorObservation(
        session_id=session.session_id, mission_id=session.mission_id, vehicle_id=vehicle,
        frame_id=f"{vehicle}:frame:{frame}", observation_id=f"{vehicle}:{person}:{frame}",
        track_id=f"track-{person}", observed_at=datetime(2026, 10, 15, tzinfo=UTC) + timedelta(seconds=frame),
        confidence=0.8, bbox_xywh=(float(person * 100), 30.0, 30.0, 80.0),
        east_m=east, north_m=0.0, latitude=12.0, longitude=77.0 + east / 108000,
        uncertainty_m=0.75, is_confirmed=True,
    ))


def test_two_close_people_remain_distinct_and_cross_drone_repeat_is_idempotent():
    session, _, registry, coordinator = setup_fleet()
    first = observe(session, registry, person=1, frame=1, east=0)
    second = observe(session, registry, person=2, frame=1, east=1)
    assert first.canonical_id != second.canonical_id
    repeat = observe(session, registry, person=1, frame=2, east=0.1, vehicle="drone_02")
    assert repeat.canonical_id == first.canonical_id
    observe(session, registry, person=1, frame=2, east=0.1, vehicle="drone_02")
    assert len(registry.confirmed_survivors()) == len(coordinator.assignments()) == 2
    assert registry.get_survivor(first.canonical_id).observation_count == 2


def test_explicit_priority_then_confirmation_age_then_id_orders_waiting_queue():
    session, states, registry, coordinator = setup_fleet(available=False)
    records = [observe(session, registry, person=i, frame=1, east=i * 20) for i in (1, 2, 3)]
    assert all(a.state == AssignmentState.QUEUED for a in coordinator.assignments())
    assert all(a.priority == ServicePriority.NORMAL for a in coordinator.assignments())
    assert all(a.priority_source == PrioritySource.DEFAULT for a in coordinator.assignments())
    # Fixed confirmation times exercise stable FIFO and canonical-ID tie-breaking.
    t = datetime(2026, 10, 15, tzinfo=UTC)
    registry._confirmed[records[0].canonical_id].confirmed_at = t + timedelta(seconds=2)
    registry._confirmed[records[1].canonical_id].confirmed_at = t
    registry._confirmed[records[2].canonical_id].confirmed_at = t
    assert [a.canonical_survivor_id for a in coordinator.assignments()] == [
        records[1].canonical_id, records[2].canonical_id, records[0].canonical_id,
    ]
    coordinator.set_priority(records[2].canonical_id, ServicePriority.CRITICAL, PrioritySource.OPERATOR)
    assert [a.canonical_survivor_id for a in coordinator.assignments()] == [
        records[2].canonical_id, records[1].canonical_id, records[0].canonical_id,
    ]
    assert [a.queue_position for a in coordinator.assignments()] == [1, 2, 3]
    states["drone_02"]["connected"] = True
    states["drone_02"]["telemetry_age_s"] = 0.1
    allocated = coordinator.reconcile_queue()
    assert len(allocated) == 1
    assert allocated[0].canonical_survivor_id == records[2].canonical_id
    assert [a.queue_position for a in coordinator.assignments() if a.state == AssignmentState.QUEUED] == [1, 2]


def test_two_servicers_receive_separate_jobs_and_unhealthy_one_stays_idle():
    session, states, registry, coordinator = setup_fleet(servicers=2)
    a = observe(session, registry, person=1, frame=1, east=0)
    b = observe(session, registry, person=2, frame=1, east=30)
    jobs = coordinator.assignments()
    assert {j.canonical_survivor_id for j in jobs} == {a.canonical_id, b.canonical_id}
    assert {j.servicer_id for j in jobs} == {"drone_02", "drone_03"}
    with pytest.raises(PermissionError):
        coordinator.allocate_job(a.canonical_id, force_servicer_id="drone_01")
    states["drone_03"]["state"] = "RETURNING"
    c = observe(session, registry, person=3, frame=1, east=60)
    queued = next(j for j in coordinator.assignments() if j.canonical_survivor_id == c.canonical_id)
    assert queued.state == AssignmentState.QUEUED
    assert "committed" in queued.reason or "ineligible" in queued.reason


def test_measurable_travel_cost_beats_unknown_vehicle_position():
    session, states, registry, coordinator = setup_fleet(servicers=2)
    states["drone_02"]["telemetry"]["latitude"] = None
    record = observe(session, registry, person=1, frame=1, east=10)
    job = coordinator.assignments()[0]
    assert job.canonical_survivor_id == record.canonical_id
    assert job.servicer_id == "drone_03"


def test_concurrent_allocation_has_one_assignment_and_priority_requires_provenance():
    session, _, registry, coordinator = setup_fleet()
    record = observe(session, registry, person=1, frame=1, east=0)
    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(lambda _: coordinator.allocate_job(record.canonical_id).assignment_id, range(40)))
    assert len(set(ids)) == len(coordinator.assignments()) == 1
    with pytest.raises(ValueError, match="provenance"):
        coordinator.set_priority(record.canonical_id, ServicePriority.HIGH, PrioritySource.DEFAULT)


def test_uncertain_target_and_stale_servicer_wait_without_dispatch():
    session, states, registry, coordinator = setup_fleet(available=False)
    record = observe(session, registry, person=1, frame=1, east=0)
    job = coordinator.assignments()[0]
    assert job.state == AssignmentState.QUEUED
    states["drone_02"]["connected"] = True
    states["drone_02"]["telemetry_age_s"] = 0.1
    registry._confirmed[record.canonical_id].uncertainty_m = 12.0
    assert coordinator.reconcile_queue() == []
    assert "uncertainty" in coordinator.get_assignment(job.assignment_id).reason


def test_stale_target_waits_until_genuine_new_observation():
    session, states, registry, coordinator = setup_fleet(available=False)
    record = observe(session, registry, person=1, frame=1, east=0)
    job = coordinator.assignments()[0]
    states["drone_02"]["connected"] = True
    states["drone_02"]["telemetry_age_s"] = 0.1
    registry._confirmed[record.canonical_id].last_received_at = datetime.now(UTC) - timedelta(minutes=6)
    assert coordinator.reconcile_queue() == []
    assert "stale" in coordinator.get_assignment(job.assignment_id).reason
    assert observe(session, registry, person=1, frame=2, east=0.1).canonical_id == record.canonical_id
    assert coordinator.reconcile_queue()[0].assignment_id == job.assignment_id


def test_verified_delivery_status_is_authoritative_and_never_redispatched():
    session, states, registry, coordinator = setup_fleet(available=False)
    record = observe(session, registry, person=1, frame=1, east=0)
    job = coordinator.assignments()[0]
    assert registry.mark_delivered(record.canonical_id).delivery_status == "DELIVERED"
    assert registry.get_survivor(record.canonical_id).delivery_status == "DELIVERED"
    states["drone_02"]["connected"] = True
    states["drone_02"]["telemetry_age_s"] = 0.1
    assert coordinator.reconcile_queue() == []
    assert coordinator.get_assignment(job.assignment_id).state == AssignmentState.QUEUED
    assert "verified delivery" in coordinator.get_assignment(job.assignment_id).reason


def test_new_session_has_no_old_priorities_jobs_or_survivors():
    session, _, registry, coordinator = setup_fleet()
    record = observe(session, registry, person=1, frame=1, east=0)
    coordinator.set_priority(record.canonical_id, ServicePriority.HIGH, PrioritySource.OPERATOR)
    registry.reset()
    coordinator.reset()
    assert registry.confirmed_survivors() == []
    assert coordinator.assignments() == []


def test_priority_api_validates_and_audits_operator_change():
    session, _, registry, coordinator = setup_fleet(available=False)
    record = observe(session, registry, person=1, frame=1, east=0)
    events = []
    coordinator.event_callback = lambda *args: events.append(args)
    app = FastAPI()
    app.include_router(router)
    app.state.multidrone_runtime = SimpleNamespace(session=session, coordinator=coordinator)
    client = TestClient(app)
    path = f"/api/multidrone/survivors/{record.canonical_id}/priority"
    assert client.post(path, json={"priority": "URGENT"}).status_code == 422
    assert client.post(path, json={"priority": "HIGH", "source": "MISSION"}).status_code == 422
    response = client.post(path, json={"priority": "HIGH"})
    assert response.status_code == 200
    assert response.json()["priority_source"] == "OPERATOR"
    assert coordinator.assignments()[0].priority == ServicePriority.HIGH
    assert events[-1][1] == "SURVIVOR_PRIORITY_CHANGED"
    session.state = "STOPPED"
    assert client.post(path, json={"priority": "LOW"}).status_code == 409


def test_stopped_result_snapshot_is_read_only_and_session_scoped():
    session, _, registry, coordinator = setup_fleet(available=False)
    record = observe(session, registry, person=1, frame=1, east=0)
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.session = session.model_copy(update={"state": "STOPPED"})
    runtime.state_machine = FleetStateMachine(FleetState.STOPPED)
    runtime.vehicles = {}
    runtime.registry = None
    runtime.coordinator = None
    runtime.plan = None
    runtime.final_coordination_snapshot = {
        "session_id": session.session_id,
        "targets": [registry.get_survivor(record.canonical_id).model_dump(mode="json")],
        "assignments": [a.model_dump(mode="json") for a in coordinator.assignments()],
    }
    assert len(runtime.status()["targets"]) == len(runtime.status()["assignments"]) == 1
    assert runtime.status()["assignments"][0]["queue_position"] == 1
    runtime.session = session.model_copy(update={"session_id": uuid4(), "state": "STOPPED"})
    assert runtime.status()["targets"] == runtime.status()["assignments"] == []
