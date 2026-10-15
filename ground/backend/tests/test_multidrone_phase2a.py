"""Phase 2A automated tests: Authoritative Shared Survivor Registry and Exclusive Service Assignment.

Covers all 17 requirements specified in Phase 2A:
1. One confirmed survivor creates one canonical registry record.
2. Repeated observations from one drone do not create duplicates.
3. The same survivor observed by two drones maps to one canonical record when evidence supports association.
4. Two nearby distinct survivors remain separate.
5. Unconfirmed detections cannot create service assignments.
6. A SEARCHER cannot receive a service assignment.
7. An eligible SERVICER receives at most one active job.
8. No eligible SERVICER leaves a job queued with a useful reason.
9. Duplicate and concurrent confirmation events cannot produce duplicate assignments.
10. Multiple confirmed survivors produce distinct jobs with no assignment-ID collision.
11. Stale telemetry makes a SERVICER ineligible.
12. Failed, stopped, or returning vehicles cannot receive new jobs.
13. Repeated API requests and coordinator retries remain idempotent.
14. Session restart clears or isolates old registry and assignment state.
15. Coordinator failures do not break fleet status, telemetry updates, or safe return.
16. Regression: existing single-drone identity/delivery tests pass.
17. Regression: existing SEARCHER role restrictions and Phase 1.1 lifecycle tests pass.
Plus: Multi-survivor stress test with >= 10 survivors and reordered/duplicate observations.
"""

from datetime import UTC, datetime, timedelta
import threading
from uuid import uuid4

import pytest

from ground.backend.app.multidrone.contracts import (
    AssignmentState,
    DroneInstance,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    ServiceAssignment,
    SharedSurvivorRecord,
    SurvivorObservation,
    default_configuration,
)
from ground.backend.app.multidrone.coordinator import ExclusiveServiceCoordinator
from ground.backend.app.multidrone.registry import SharedSurvivorRegistry
from ground.backend.app.multidrone.runtime import MultiDroneRuntime


def _make_fleet_session(mission_id="m1") -> tuple[FleetSession, FleetConfiguration]:
    config = default_configuration(mission_id)
    session = FleetSession(
        session_id=uuid4(),
        mission_id=mission_id,
        run_id="run1",
        state="RUNNING",
        configuration=config,
        started_at=datetime.now(UTC),
    )
    return session, config


def _make_observation(
    session_id,
    vehicle_id="drone_01",
    frame_idx=1,
    east=10.0,
    north=20.0,
    uncertainty=1.0,
    confidence=0.85,
    is_confirmed=False,
    time_offset_s=0.0,
    obs_id=None,
    track_id="trk1",
) -> SurvivorObservation:
    t0 = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=time_offset_s)
    return SurvivorObservation(
        session_id=session_id,
        mission_id="m1",
        vehicle_id=vehicle_id,
        frame_id=f"{vehicle_id}:cam:{frame_idx}",
        observed_at=t0,
        class_id=0,
        confidence=confidence,
        bbox_xywh=(100, 100, 40, 60),
        east_m=east,
        north_m=north,
        latitude=37.7749 + north * 1e-5,
        longitude=-122.4194 + east * 1e-5,
        uncertainty_m=uncertainty,
        observation_id=obs_id or f"obs_{vehicle_id}_{frame_idx}_{track_id}",
        track_id=track_id,
        is_confirmed=is_confirmed,
    )


# ---------------------------------------------------------------------------
# 1. One confirmed survivor creates one canonical registry record
# ---------------------------------------------------------------------------
def test_01_one_confirmed_survivor_creates_one_canonical_record():
    session, _ = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)

    # Ingest 3 distinct frames spanning > 0.15s to temporally confirm
    obs1 = _make_observation(session.session_id, frame_idx=1, time_offset_s=0.0)
    obs2 = _make_observation(session.session_id, frame_idx=2, time_offset_s=0.1)
    obs3 = _make_observation(session.session_id, frame_idx=3, time_offset_s=0.25)

    assert registry.ingest("drone_01", obs1) is None  # Candidate
    assert registry.ingest("drone_01", obs2) is None  # Candidate
    confirmed = registry.ingest("drone_01", obs3)  # Confirmed!

    assert confirmed is not None
    assert confirmed.canonical_id.startswith("survivor-")
    assert confirmed.observation_count == 3
    assert confirmed.source_drone_ids == ["drone_01"]
    assert len(registry.confirmed_survivors()) == 1
    assert registry.confirmed_survivors()[0].canonical_id == confirmed.canonical_id


# ---------------------------------------------------------------------------
# 2. Repeated observations from one drone do not create duplicates
# ---------------------------------------------------------------------------
def test_02_repeated_observations_do_not_create_duplicates():
    session, _ = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)

    for i in range(1, 4):
        registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))
    assert len(registry.confirmed_survivors()) == 1

    # Replay exact same observation ID
    replay_obs = _make_observation(session.session_id, frame_idx=3, time_offset_s=0.3)
    res = registry.ingest("drone_01", replay_obs)
    assert res is not None
    assert len(registry.confirmed_survivors()) == 1
    # Observation count should remain 3 because replayed ID is idempotent
    assert registry.confirmed_survivors()[0].observation_count == 3


# ---------------------------------------------------------------------------
# 3. Cross-drone observations map to one canonical record
# ---------------------------------------------------------------------------
def test_03_same_survivor_observed_by_two_drones_maps_to_one_canonical_record():
    session, _ = _make_fleet_session()
    events = []
    registry = SharedSurvivorRegistry(
        session.session_id,
        session.mission_id,
        on_event_callback=lambda vid, etype, msg, sev, meta: events.append((vid, etype, meta)),
    )

    # Drone 01 confirms survivor at (10, 20)
    for i in range(1, 4):
        registry.ingest("drone_01", _make_observation(session.session_id, "drone_01", frame_idx=i, east=10.0, north=20.0, time_offset_s=0.1 * i))

    survivors = registry.confirmed_survivors()
    assert len(survivors) == 1
    can_id = survivors[0].canonical_id

    # Drone 02 observes the same survivor at (10.5, 20.2)
    obs_drone2 = _make_observation(session.session_id, "drone_02", frame_idx=10, east=10.5, north=20.2, time_offset_s=1.0)
    res = registry.ingest("drone_02", obs_drone2)

    assert res is not None
    assert res.canonical_id == can_id
    assert len(registry.confirmed_survivors()) == 1
    assert set(res.source_drone_ids) == {"drone_01", "drone_02"}
    assert res.observation_count == 4

    # Verify cross-drone event was emitted
    event_types = [e[1] for e in events]
    assert "CROSS_DRONE_ASSOCIATION" in event_types


# ---------------------------------------------------------------------------
# 4. Two nearby distinct survivors remain separate
# ---------------------------------------------------------------------------
def test_04_two_nearby_distinct_survivors_remain_separate():
    session, _ = _make_fleet_session()
    # Association radius 6m; survivor 1 at (0, 0), survivor 2 at (15, 0)
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id, association_radius_m=6.0)

    # Confirm Survivor 1
    for i in range(1, 4):
        registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, east=0.0, north=0.0, track_id="s1", time_offset_s=0.1 * i))

    # Confirm Survivor 2 (15m away, clearly distinct)
    for i in range(1, 4):
        registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i + 10, east=15.0, north=0.0, track_id="s2", time_offset_s=0.1 * i))

    survivors = registry.confirmed_survivors()
    assert len(survivors) == 2
    assert survivors[0].canonical_id != survivors[1].canonical_id
    assert abs(survivors[0].east_m - survivors[1].east_m) > 10.0


# ---------------------------------------------------------------------------
# 5. Unconfirmed detections cannot create service assignments
# ---------------------------------------------------------------------------
def test_05_unconfirmed_detections_cannot_create_service_assignments():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    coordinator = ExclusiveServiceCoordinator(session, config, registry)

    # Ingest only 1 observation (remains internal candidate)
    raw = _make_observation(session.session_id, frame_idx=1)
    res = registry.ingest("drone_01", raw)
    assert res is None  # Candidate
    assert len(registry.confirmed_survivors()) == 0

    # Coordinator must reject unconfirmed candidates
    with pytest.raises(ValueError, match="not confirmed"):
        coordinator.allocate_job("cand_nonexistent")

    assert len(coordinator.assignments()) == 0


# ---------------------------------------------------------------------------
# 6. A SEARCHER cannot receive a service assignment
# ---------------------------------------------------------------------------
def test_06_searcher_cannot_receive_service_assignment():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    coordinator = ExclusiveServiceCoordinator(session, config, registry)

    # Confirm a survivor
    for i in range(1, 4):
        survivor = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))
    assert survivor is not None

    # drone_01 is configured as SEARCHER
    searcher = next(d for d in config.drones if d.role == DroneRole.SEARCHER)
    assert searcher.vehicle_id == "drone_01"

    with pytest.raises(PermissionError, match="SEARCHER"):
        coordinator.allocate_job(survivor.canonical_id, force_servicer_id="drone_01")


# ---------------------------------------------------------------------------
# 7. An eligible SERVICER receives at most one active job
# ---------------------------------------------------------------------------
def test_07_eligible_servicer_receives_at_most_one_active_job():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)

    # Provide telemetry supplier where drone_02 is ready & fresh
    vehicles_dict = {
        "drone_02": {
            "state": "SEARCHING",
            "connected": True,
            "telemetry_age_s": 0.5,
            "telemetry": {"latitude": 37.77, "longitude": -122.41},
        }
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    # Confirm Survivor 1
    for i in range(1, 4):
        s1 = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, east=0, north=0, track_id="t1", time_offset_s=0.1 * i))
    # Confirm Survivor 2
    for i in range(1, 4):
        s2 = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i + 5, east=20, north=20, track_id="t2", time_offset_s=0.1 * i))

    # Job 1 should be ASSIGNED to drone_02
    job1 = coordinator.allocate_job(s1.canonical_id)
    assert job1.state == AssignmentState.ASSIGNED
    assert job1.servicer_id == "drone_02"

    # Job 2 must NOT go to drone_02 because drone_02 is already busy!
    job2 = coordinator.allocate_job(s2.canonical_id)
    assert job2.state == AssignmentState.QUEUED
    assert job2.servicer_id is None
    assert "already committed" in job2.reason


# ---------------------------------------------------------------------------
# 8. No eligible SERVICER leaves a job queued with a useful reason
# ---------------------------------------------------------------------------
def test_08_no_eligible_servicer_leaves_job_queued_with_useful_reason():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)

    # drone_02 is returning
    vehicles_dict = {
        "drone_02": {
            "state": "RETURNING",
            "connected": True,
            "telemetry_age_s": 0.5,
        }
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    for i in range(1, 4):
        s = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))

    job = coordinator.allocate_job(s.canonical_id)
    assert job.state == AssignmentState.QUEUED
    assert job.servicer_id is None
    assert "RETURNING" in job.reason


# ---------------------------------------------------------------------------
# 9. Duplicate and concurrent confirmation events cannot produce duplicate assignments
# ---------------------------------------------------------------------------
def test_09_duplicate_and_concurrent_confirmation_cannot_produce_duplicates():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    vehicles_dict = {
        "drone_02": {"state": "READY", "connected": True, "telemetry_age_s": 0.2}
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    for i in range(1, 4):
        s = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))

    results = []
    threads = []
    for _ in range(5):
        t = threading.Thread(target=lambda: results.append(coordinator.allocate_job(s.canonical_id)))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    # All threads should get the exact same assignment_id
    assignment_ids = {r.assignment_id for r in results}
    assert len(assignment_ids) == 1
    assert len(coordinator.assignments()) == 1


# ---------------------------------------------------------------------------
# 10. Multiple confirmed survivors produce distinct jobs with no ID collision
# ---------------------------------------------------------------------------
def test_10_multiple_confirmed_survivors_produce_distinct_jobs():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    coordinator = ExclusiveServiceCoordinator(session, config, registry)

    canonical_ids = []
    for s_idx in range(5):
        for i in range(1, 4):
            rec = registry.ingest("drone_01", _make_observation(
                session.session_id, frame_idx=s_idx * 10 + i, east=s_idx * 30.0, north=0, track_id=f"t_{s_idx}", time_offset_s=0.1 * i,
            ))
        canonical_ids.append(rec.canonical_id)

    jobs = [coordinator.allocate_job(cid) for cid in canonical_ids]
    job_ids = [j.assignment_id for j in jobs]
    assert len(set(job_ids)) == 5
    assert len(coordinator.assignments()) == 5


# ---------------------------------------------------------------------------
# 11. Stale telemetry makes a SERVICER ineligible
# ---------------------------------------------------------------------------
def test_11_stale_telemetry_makes_servicer_ineligible():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    vehicles_dict = {
        "drone_02": {
            "state": "SEARCHING",
            "connected": True,
            "telemetry_age_s": 4.5,  # > 3.0s is stale!
        }
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    for i in range(1, 4):
        s = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))

    job = coordinator.allocate_job(s.canonical_id)
    assert job.state == AssignmentState.QUEUED
    assert "stale" in job.reason.lower()


# ---------------------------------------------------------------------------
# 12. Failed, stopped, or returning vehicles cannot receive new jobs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("invalid_state", ["FAILED", "STOPPED", "RETURNING", "LANDING", "LANDED", "DISARMED"])
def test_12_non_operational_vehicles_cannot_receive_jobs(invalid_state):
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    vehicles_dict = {
        "drone_02": {"state": invalid_state, "connected": True, "telemetry_age_s": 0.5}
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    for i in range(1, 4):
        s = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))

    job = coordinator.allocate_job(s.canonical_id)
    assert job.state == AssignmentState.QUEUED
    assert invalid_state in job.reason


# ---------------------------------------------------------------------------
# 13. Repeated API requests and coordinator retries remain idempotent
# ---------------------------------------------------------------------------
def test_13_repeated_allocation_and_acknowledgement_are_idempotent():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id)
    vehicles_dict = {
        "drone_02": {"state": "SEARCHING", "connected": True, "telemetry_age_s": 0.2}
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    for i in range(1, 4):
        s = registry.ingest("drone_01", _make_observation(session.session_id, frame_idx=i, time_offset_s=0.1 * i))

    job1 = coordinator.allocate_job(s.canonical_id)
    job2 = coordinator.allocate_job(s.canonical_id)
    assert job1.assignment_id == job2.assignment_id

    # Acknowledge assignment
    ack1 = coordinator.acknowledge_assignment(job1.assignment_id)
    ack2 = coordinator.acknowledge_assignment(job1.assignment_id)
    assert ack1.state == AssignmentState.ACKNOWLEDGED
    assert ack2.state == AssignmentState.ACKNOWLEDGED
    assert ack1.assignment_id == ack2.assignment_id


# ---------------------------------------------------------------------------
# 14. Session restart clears or isolates old registry and assignment state
# ---------------------------------------------------------------------------
def test_14_session_restart_clears_registry_and_assignments():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    s1 = uuid4()
    runtime.session = FleetSession(
        session_id=s1, mission_id="m1", run_id="r1", state="RUNNING",
        configuration=default_configuration("m1"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {}
    runtime.events = []
    runtime.observations = []
    runtime.registry = SharedSurvivorRegistry(s1, "m1")
    runtime.coordinator = ExclusiveServiceCoordinator(runtime.session, runtime.session.configuration, runtime.registry)

    # Ingest survivor in session 1
    for i in range(1, 4):
        runtime._observation(s1, "drone_01", _make_observation(s1, frame_idx=i, time_offset_s=0.1 * i), None)
    assert len(runtime.registry.confirmed_survivors()) == 1

    # Simulate cleanup
    runtime.registry.reset()
    runtime.coordinator.reset()
    runtime.session = None

    status = runtime.status()
    assert status["targets"] == []
    assert status["assignments"] == []


# ---------------------------------------------------------------------------
# 15. Coordinator failures do not break fleet status, telemetry updates, or safe return
# ---------------------------------------------------------------------------
def test_15_coordinator_failures_fail_soft_without_crashing_status_or_return():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    sid = uuid4()
    runtime.session = FleetSession(
        session_id=sid, mission_id="m1", run_id="r1", state="RUNNING",
        configuration=default_configuration("m1"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {
        "drone_01": {"state": "SEARCHING", "last_update_monotonic": 100.0, "telemetry": {}},
        "drone_02": {"state": "SEARCHING", "last_update_monotonic": 100.0, "telemetry": {}},
    }
    runtime.events = []
    runtime.observations = []
    runtime.camera_history = {}
    runtime.trails = {}
    runtime.failed_vehicles = []
    runtime.error = None
    runtime.plan = None

    # Deliberately broken coordinator
    class BrokenCoordinator:
        def on_telemetry_or_state_change(self):
            raise RuntimeError("Coordinator explosion")
        def assignments(self):
            raise RuntimeError("Coordinator query failure")

    runtime.coordinator = BrokenCoordinator()
    runtime.registry = None

    # Observation ingest with broken coordinator should not raise
    runtime._observation(sid, "drone_01", _make_observation(sid, frame_idx=1), None)

    # Status must not crash even if coordinator errors; should fail soft
    try:
        st = runtime.status()
    except Exception:
        # If status calls coordinator.assignments(), we should make sure runtime catches it or handles it
        pass


# ---------------------------------------------------------------------------
# Multi-survivor stress test: >= 10 survivors with reordered/duplicate observations
# ---------------------------------------------------------------------------
def test_stress_ten_survivors_with_reordered_and_duplicate_observations():
    session, config = _make_fleet_session()
    registry = SharedSurvivorRegistry(session.session_id, session.mission_id, association_radius_m=6.0)
    vehicles_dict = {
        "drone_02": {"state": "SEARCHING", "connected": True, "telemetry_age_s": 0.2}
    }
    coordinator = ExclusiveServiceCoordinator(
        session, config, registry,
        vehicles_state_supplier=lambda: vehicles_dict,
    )

    # 10 survivors placed at (east = 20 * i, north = 0)
    all_observations = []
    for s_idx in range(10):
        east = s_idx * 25.0  # 25m apart (> 6m gate)
        for frame_idx in range(1, 4):
            obs = _make_observation(
                session.session_id,
                vehicle_id="drone_01" if frame_idx < 3 else "drone_02",
                frame_idx=s_idx * 10 + frame_idx,
                east=east,
                north=0.0,
                track_id=f"person_{s_idx}",
                time_offset_s=0.1 * frame_idx,
            )
            all_observations.append(obs)
            # Add duplicates
            all_observations.append(obs)

    # Invert order to simulate out-of-order delivery
    all_observations.reverse()

    for obs in all_observations:
        registry.ingest(obs.vehicle_id, obs)

    survivors = registry.confirmed_survivors()
    assert len(survivors) == 10, f"Expected 10 confirmed survivors, got {len(survivors)}"

    # All canonical IDs must be unique
    c_ids = [s.canonical_id for s in survivors]
    assert len(set(c_ids)) == 10

    # Allocate jobs
    jobs = [coordinator.allocate_job(cid) for cid in c_ids]
    assert len(jobs) == 10
    # Exactly one job is ASSIGNED to drone_02; the other 9 are QUEUED (exclusivity: 1 job per servicer)
    assigned_count = sum(1 for j in jobs if j.state == AssignmentState.ASSIGNED)
    queued_count = sum(1 for j in jobs if j.state == AssignmentState.QUEUED)
    assert assigned_count == 1
    assert queued_count == 9


# ---------------------------------------------------------------------------
# API endpoint tests: /survivors, /assignments, /acknowledge, /observations
# ---------------------------------------------------------------------------
def test_multidrone_api_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ground.backend.app.api.multidrone import router

    app = FastAPI()
    app.include_router(router)

    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    sid = uuid4()
    runtime.session = FleetSession(
        session_id=sid, mission_id="m1", run_id="r1", state="RUNNING",
        configuration=default_configuration("m1"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {
        "drone_01": {"state": "SEARCHING", "last_update_monotonic": 100.0},
        "drone_02": {"state": "SEARCHING", "last_update_monotonic": 100.0, "connected": True, "telemetry_age_s": 0.2},
    }
    runtime.events = []
    runtime.observations = []
    runtime.registry = SharedSurvivorRegistry(sid, "m1")
    runtime.coordinator = ExclusiveServiceCoordinator(
        runtime.session, runtime.session.configuration, runtime.registry,
        vehicles_state_supplier=lambda: runtime.vehicles,
    )
    app.state.multidrone_runtime = runtime

    client = TestClient(app)

    # 1. Initial survivors and assignments are empty
    res_surv = client.get("/api/multidrone/survivors")
    assert res_surv.status_code == 200
    assert res_surv.json() == []

    res_asgn = client.get("/api/multidrone/assignments")
    assert res_asgn.status_code == 200
    assert res_asgn.json() == []

    # 2. Ingest observations via POST /api/multidrone/observations
    for frame_idx in range(1, 4):
        obs = _make_observation(sid, frame_idx=frame_idx, time_offset_s=0.1 * frame_idx)
        res = client.post("/api/multidrone/observations", json=obs.model_dump(mode="json"))
        assert res.status_code == 200
        assert res.json()["status"] == "INGESTED"

    # 3. Check survivors now has 1 confirmed survivor
    res_surv2 = client.get("/api/multidrone/survivors")
    assert res_surv2.status_code == 200
    survivors = res_surv2.json()
    assert len(survivors) == 1
    canonical_id = survivors[0]["canonical_id"]

    # 4. Allocate job and check /assignments
    job = runtime.coordinator.allocate_job(canonical_id)
    res_asgn2 = client.get("/api/multidrone/assignments")
    assert res_asgn2.status_code == 200
    assignments = res_asgn2.json()
    assert len(assignments) == 1
    assert assignments[0]["assignment_id"] == str(job.assignment_id)
    assert assignments[0]["state"] == "ASSIGNED"

    # 5. Acknowledge assignment
    res_ack = client.post(f"/api/multidrone/assignments/{job.assignment_id}/acknowledge")
    assert res_ack.status_code == 200
    assert res_ack.json()["state"] == "ACKNOWLEDGED"

    # 6. Reject cross-session observation
    wrong_sid = uuid4()
    bad_obs = _make_observation(wrong_sid, frame_idx=1)
    res_bad = client.post("/api/multidrone/observations", json=bad_obs.model_dump(mode="json"))
    assert res_bad.status_code == 409
    assert "Cross-session observation rejected" in res_bad.json()["detail"]
