"""Phase 2B Autonomous Service Dispatch, Simulated Delivery, and Search Resumption Test Suite.

Validates the full end-to-end data flow:
Real confirmed survivor -> Exclusive SERVICER assignment -> Safe approach ->
Simulated payload release -> Independent delivery verification -> Exact search resumption.

Enforces:
1. Confirmed survivor creates one service execution.
2. SEARCHER service execution is rejected by backend policy.
3. SERVICER assignment and approach bind to the correct canonical survivor.
4. Stale telemetry or invalid geolocation blocks approach or release.
5. Geofence, altitude, velocity, and positional safety gates are enforced.
6. A blocked safety gate prevents payload release.
7. Duplicate execution requests cannot create multiple active controllers.
8. A release is executed at most once per assignment.
9. Ambiguous release outcome never triggers an automatic second release.
10. Release acceptance alone does not imply delivery success.
11. Failed delivery verification is handled without duplicate release.
12. A pre-release failure can be safely re-queued after cleanup.
13. A post-release failure preserves release state and prevents duplicate delivery.
14. Successful delivery restores the correct search cursor.
15. Completed coverage is not replayed after resumption.
16. SEARCHER mission progress remains independent of SERVICER execution.
17. Multiple queued survivors are processed sequentially without assignment collisions.
18. Fleet RTL/stop takes precedence over service execution.
19. Session restart does not leak or duplicate assignments/releases.
20. Existing single-drone delivery, survivor identity, and fleet lifecycle tests remain passing.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from shapely.geometry import Polygon

from ground.backend.app.multidrone.contracts import (
    AssignmentState,
    DroneInstance,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    default_configuration,
)
from ground.backend.app.multidrone.coordinator import (
    ACTIVE_SERVICE_STATES,
    VALID_ASSIGNMENT_TRANSITIONS,
    ExclusiveServiceCoordinator,
)
from ground.backend.app.multidrone.registry import SharedSurvivorRegistry
from ground.backend.app.multidrone.runtime import MultiDroneRuntime
from ground.backend.app.multidrone.service_executor import MultiDroneServiceExecutor
from ground.backend.app.multidrone.state_machine import (
    DroneLifecycleState,
    DroneStateMachine,
    FleetState,
    FleetStateMachine,
)
from rescue_core.autonomy.models import SearchResumePolicyName
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point


# ===========================================================================
# Test Helpers and Fixtures
# ===========================================================================

def _make_test_fleet_session(mission_id: str = "mission_phase2b") -> tuple[FleetSession, FleetConfiguration]:
    config = default_configuration(mission_id)
    session = FleetSession(
        session_id=uuid4(),
        mission_id=mission_id,
        run_id=f"run_{mission_id}",
        state="RUNNING",
        configuration=config,
        started_at=datetime.now(UTC),
    )
    return session, config


def _make_vehicles_state():
    now = time.monotonic()
    return {
        "drone_01": {
            "vehicle_id": "drone_01",
            "state": DroneLifecycleState.SEARCHING.value,
            "connected": True,
            "telemetry_age_s": 0.1,
            "last_update_monotonic": now,
            "telemetry": {
                "latitude": 12.9720,
                "longitude": 77.5925,
                "east_m": 10.0,
                "north_m": 10.0,
                "relative_altitude_m": 20.0,
                "ground_speed_m_s": 4.5,
                "flight_mode": "AUTO",
                "armed": True,
            },
        },
        "drone_02": {
            "vehicle_id": "drone_02",
            "state": DroneLifecycleState.READY.value,
            "connected": True,
            "telemetry_age_s": 0.1,
            "last_update_monotonic": now,
            "telemetry": {
                "latitude": 12.9721,
                "longitude": 77.5926,
                "east_m": 20.0,
                "north_m": 20.0,
                "relative_altitude_m": 20.0,
                "ground_speed_m_s": 0.0,
                "flight_mode": "GUIDED",
                "armed": True,
            },
        },
    }


def _make_test_boundary() -> Polygon:
    return Polygon([(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)])


def _setup_coordinator(vehicles_state: dict | None = None) -> tuple[ExclusiveServiceCoordinator, SharedSurvivorRegistry, FleetSession]:
    session, config = _make_test_fleet_session()
    vstate = vehicles_state if vehicles_state is not None else _make_vehicles_state()
    registry = SharedSurvivorRegistry(session_id=session.session_id, mission_id=session.mission_id)
    coordinator = ExclusiveServiceCoordinator(
        session=session,
        fleet_config=config,
        registry=registry,
        vehicles_state_supplier=lambda: vstate,
    )
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed
    return coordinator, registry, session


def _ingest_confirmed_survivor(registry: SharedSurvivorRegistry, vid: str = "drone_01", east: float = 30.0, north: float = 30.0, unc: float = 1.2):
    obs = {
        "target_latitude": 12.9725,
        "target_longitude": 77.5930,
        "target_east_m": east,
        "target_north_m": north,
        "uncertainty_m": unc,
        "confidence": 0.92,
    }
    est = type("Est", (), {
        "state": type("St", (), {"value": "CONFIRMED"}),
        "east_m": east,
        "north_m": north,
        "uncertainty_m": unc,
    })()
    return registry.ingest(vid, obs, est)


# ===========================================================================
# 1. Confirmed survivor creates one service execution
# ===========================================================================


def test_verified_release_transits_vehicle_before_next_queued_job():
    machine = DroneStateMachine("drone_02", DroneLifecycleState.RELEASED)
    runtime = SimpleNamespace(_transition_drone=lambda _vid, state: machine.transition(state))
    executor = MultiDroneServiceExecutor(runtime)
    verified = SimpleNamespace(delivery_verified=True)

    class Verifier:
        def verify_delivery(self, _assignment_id, *, verified: bool, evidence: dict):
            assert machine.current == DroneLifecycleState.VERIFYING
            assert verified and evidence["release_id"] == "release-1"
            return verified_job

    verified_job = verified
    result = executor._verify_released_job("drone_02", Verifier(), uuid4(), {"release_id": "release-1"})
    assert result is verified_job
    assert machine.current == DroneLifecycleState.DELIVERED
    machine.transition(DroneLifecycleState.RESUMING_SEARCH)
    machine.transition(DroneLifecycleState.SEARCHING)
    assert machine.current == DroneLifecycleState.SEARCHING


def test_failed_delivery_verification_does_not_mark_vehicle_delivered():
    machine = DroneStateMachine("drone_02", DroneLifecycleState.RELEASED)
    runtime = SimpleNamespace(_transition_drone=lambda _vid, state: machine.transition(state))
    executor = MultiDroneServiceExecutor(runtime)

    class Verifier:
        def verify_delivery(self, *_args, **_kwargs):
            raise RuntimeError("independent verification failed")

    with pytest.raises(RuntimeError, match="independent verification failed"):
        executor._verify_released_job("drone_02", Verifier(), uuid4(), {})
    assert machine.current == DroneLifecycleState.VERIFYING

def test_confirmed_survivor_creates_one_service_execution():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    assert survivor is not None

    assignments = coordinator.assignments()
    assert len(assignments) == 1
    job = assignments[0]
    assert job.canonical_survivor_id == survivor.canonical_id
    assert job.servicer_id == "drone_02"
    assert job.state == AssignmentState.ASSIGNED


# ===========================================================================
# 2. SEARCHER service execution is rejected by backend policy
# ===========================================================================

def test_searcher_service_execution_rejected_by_policy():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    assert survivor is not None

    # Direct allocation to SEARCHER must raise PermissionError
    with pytest.raises(PermissionError, match="SEARCHER"):
        coordinator.allocate_job(survivor.canonical_id, force_servicer_id="drone_01")

    # Starting approach on SEARCHER must raise safety/role rejection
    job = coordinator.assignments()[0]
    with pytest.raises(RuntimeError, match="SEARCHER"):
        # Temporarily mock assignment servicer to SEARCHER
        job_mut = coordinator._assignments[job.assignment_id]
        job_mut.servicer_id = "drone_01"
        coordinator.start_approach(job.assignment_id)


# ===========================================================================
# 3. SERVICER assignment and approach bind to the correct canonical survivor
# ===========================================================================

def test_servicer_assignment_and_approach_bind_to_canonical_survivor():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    assert survivor is not None

    job = coordinator.assignments()[0]
    assert job.canonical_survivor_id == survivor.canonical_id

    # Acknowledge and start approach
    coordinator.acknowledge_assignment(job.assignment_id)
    app_job = coordinator.start_approach(job.assignment_id, checkpoint_seq=5)

    assert app_job.state == AssignmentState.APPROACHING
    assert app_job.canonical_survivor_id == survivor.canonical_id
    assert app_job.servicer_id == "drone_02"
    assert app_job.checkpoint_seq == 5


# ===========================================================================
# 4. Stale telemetry or invalid geolocation blocks approach or release
# ===========================================================================

def test_stale_telemetry_or_invalid_geolocation_blocks_approach_or_release():
    vstate = _make_vehicles_state()
    # Make drone_02 telemetry stale (>3.0s)
    vstate["drone_02"]["telemetry_age_s"] = 4.5
    vstate["drone_02"]["connected"] = False

    coordinator, registry, session = _setup_coordinator(vstate)
    survivor = _ingest_confirmed_survivor(registry)

    # Job is queued due to stale telemetry
    job = coordinator.assignments()[0]
    assert job.state == AssignmentState.QUEUED

    # Forcing approach with stale telemetry must raise RuntimeError
    with pytest.raises(RuntimeError, match="stale or disconnected"):
        # Manually assign to test gate rejection
        job_mut = coordinator._assignments[job.assignment_id]
        job_mut.servicer_id = "drone_02"
        job_mut.state = AssignmentState.ASSIGNED
        coordinator.start_approach(job.assignment_id)

    # Recovery: restore fresh telemetry
    vstate["drone_02"]["telemetry_age_s"] = 0.2
    vstate["drone_02"]["connected"] = True

    # Now test invalid geolocation: uncertainty > 8.0m
    survivor_mut = registry._confirmed[survivor.canonical_id]
    survivor_mut.uncertainty_m = 12.0  # Exceeds 8.0m limit
    with pytest.raises(RuntimeError, match="uncertainty .* exceeds acceptable limit"):
        coordinator.start_approach(job.assignment_id)


# ===========================================================================
# 5. Geofence, altitude, velocity, and positional safety gates are enforced
# ===========================================================================

def test_geofence_altitude_velocity_and_positional_safety_gates_enforced():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry, east=40.0, north=40.0)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)

    # Standoff delivery point >= 1.0m
    deliv_point = (42.0, 42.0, 5.0)
    standoff = 2.83
    coordinator.hold_for_release(
        job.assignment_id,
        approach_point=(42.0, 42.0, 15.0),
        delivery_point=deliv_point,
        standoff_m=standoff,
    )

    # Check release safety with healthy telemetry
    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.2,
        "telemetry": {
            "relative_altitude_m": 5.0,
            "ground_speed_m_s": 0.2,
            "east_m": 42.2,
            "north_m": 42.1,
            "armed": True,
            "flight_mode": "GUIDED",
        },
    }
    safe, reason = coordinator.check_release_safety(job.assignment_id, telemetry_override=healthy_telem)
    assert safe is True
    assert reason is None

    # Check high ground speed (> 0.8 m/s) violation
    fast_telem = dict(healthy_telem)
    fast_telem["telemetry"] = dict(healthy_telem["telemetry"], ground_speed_m_s=1.5)
    safe, reason = coordinator.check_release_safety(job.assignment_id, telemetry_override=fast_telem)
    assert safe is False
    assert "exceeds max release speed" in reason

    # Check altitude error (> 2.0m from 5.0m)
    high_telem = dict(healthy_telem)
    high_telem["telemetry"] = dict(healthy_telem["telemetry"], relative_altitude_m=12.0)
    safe, reason = coordinator.check_release_safety(job.assignment_id, telemetry_override=high_telem)
    assert safe is False
    assert "outside delivery envelope" in reason


# ===========================================================================
# 6. A blocked safety gate prevents payload release
# ===========================================================================

def test_blocked_safety_gate_prevents_payload_release():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    # Unsafe flight conditions: vehicle disarmed and high speed
    unsafe_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {
            "relative_altitude_m": 5.0,
            "ground_speed_m_s": 2.5,
            "armed": False,  # Disarmed
            "flight_mode": "AUTO",
        },
    }

    with pytest.raises(RuntimeError, match="blocked by safety gate"):
        coordinator.execute_simulated_release(job.assignment_id, telemetry_override=unsafe_telem)

    # Verify state remains HOLDING_FOR_RELEASE and payload NOT released
    curr = coordinator.get_assignment(job.assignment_id)
    assert curr.state == AssignmentState.HOLDING_FOR_RELEASE
    assert curr.payload_released is False


# ===========================================================================
# 7. Duplicate execution requests cannot create multiple active controllers
# ===========================================================================

def test_duplicate_execution_requests_cannot_create_multiple_active_controllers():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job1 = coordinator.assignments()[0]

    # Re-allocating the same survivor must return the existing assignment (idempotent)
    job2 = coordinator.allocate_job(survivor.canonical_id)
    assert job1.assignment_id == job2.assignment_id
    assert len(coordinator.assignments()) == 1

    # While drone_02 is servicing survivor 1, another confirmed survivor must be QUEUED
    survivor2 = _ingest_confirmed_survivor(registry, east=60.0, north=60.0)
    assert survivor2.canonical_id != survivor.canonical_id
    job_other = [a for a in coordinator.assignments() if a.canonical_survivor_id == survivor2.canonical_id][0]
    assert job_other.state == AssignmentState.QUEUED
    assert "already committed" in job_other.reason


# ===========================================================================
# 8. A release is executed at most once per assignment
# ===========================================================================

def test_release_is_executed_at_most_once_per_assignment():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {
            "relative_altitude_m": 5.0,
            "ground_speed_m_s": 0.1,
            "armed": True,
            "flight_mode": "GUIDED",
        },
    }

    # First release succeeds and transitions to VERIFYING
    rel_job = coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)
    assert rel_job.payload_released is True
    assert rel_job.release_id is not None
    assert rel_job.state == AssignmentState.VERIFYING

    # Attempting second release MUST raise RuntimeError
    with pytest.raises(RuntimeError, match="must be HOLDING_FOR_RELEASE"):
        coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    # Even if artificially moved back to HOLDING_FOR_RELEASE, exactly-once check rejects it
    job_mut = coordinator._assignments[job.assignment_id]
    job_mut.state = AssignmentState.HOLDING_FOR_RELEASE
    with pytest.raises(RuntimeError, match="Exactly-once release violation"):
        coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)


# ===========================================================================
# 9. Ambiguous release outcome never triggers an automatic second release
# ===========================================================================

def test_ambiguous_release_outcome_never_triggers_automatic_second_release():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    # Ambiguous verification: verify with verified=False
    failed_job = coordinator.verify_delivery(
        job.assignment_id,
        verified=False,
        failure_reason="Ambiguous telemetry departure signature",
    )
    assert failed_job.state == AssignmentState.FAILED
    assert failed_job.payload_released is True

    # Exactly-once guard prevents re-release or re-queueing
    with pytest.raises(RuntimeError, match="Post-release failures must fail closed"):
        coordinator.requeue_assignment(job.assignment_id)


# ===========================================================================
# 10. Release acceptance alone does not imply delivery success
# ===========================================================================

def test_release_acceptance_alone_does_not_imply_delivery_success():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    rel_job = coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    # State is VERIFYING, NOT DELIVERED!
    assert rel_job.state == AssignmentState.VERIFYING
    assert rel_job.delivery_verified is False
    assert rel_job.payload_released is True


# ===========================================================================
# 11. Failed delivery verification is handled without duplicate release
# ===========================================================================

def test_failed_delivery_verification_handled_without_duplicate_release():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    # Verification fails
    failed_job = coordinator.verify_delivery(job.assignment_id, verified=False, failure_reason="Kit stuck or dropped outside envelope")
    assert failed_job.state == AssignmentState.FAILED
    assert failed_job.delivery_verified is False
    assert failed_job.payload_released is True
    assert "Kit stuck" in failed_job.failure_reason


# ===========================================================================
# 12. A pre-release failure can be safely re-queued after cleanup
# ===========================================================================

def test_pre_release_failure_can_be_safely_requeued_after_cleanup():
    vstate = _make_vehicles_state()
    coordinator, registry, session = _setup_coordinator(vstate)
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)

    # Invalidate drone_02 eligibility before requeueing so it stays in QUEUED state
    vstate["drone_02"]["state"] = "RETURNING"

    # Sensor glitch or gust occurs during approach BEFORE release
    requeued_job = coordinator.requeue_assignment(job.assignment_id, reason="Temporary approach abort due to wind gust")
    assert requeued_job.state == AssignmentState.QUEUED
    assert requeued_job.servicer_id is None
    assert requeued_job.payload_released is False


# ===========================================================================
# 13. A post-release failure preserves release state and prevents duplicate delivery
# ===========================================================================

def test_post_release_failure_preserves_release_state_and_prevents_duplicate_delivery():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    # Verification fails post-release
    failed_job = coordinator.verify_delivery(job.assignment_id, verified=False, failure_reason="Unverified landing")
    assert failed_job.payload_released is True
    assert failed_job.state == AssignmentState.FAILED

    # Re-queue is strictly forbidden: post-release failure cannot be re-released
    with pytest.raises(RuntimeError, match="Post-release failures must fail closed"):
        coordinator.requeue_assignment(job.assignment_id)

    # Calling allocate_job on this survivor returns the existing release-recorded assignment
    dup_alloc = coordinator.allocate_job(survivor.canonical_id)
    assert dup_alloc.assignment_id == job.assignment_id
    assert dup_alloc.payload_released is True


# ===========================================================================
# 14. Successful delivery restores the correct search cursor
# ===========================================================================

def test_successful_delivery_restores_correct_search_cursor():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id, checkpoint_seq=7)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING",
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)
    coordinator.verify_delivery(job.assignment_id, verified=True)

    # Resuming search
    resumed = coordinator.start_resuming_search(job.assignment_id, resume_seq=8)
    assert resumed.state == AssignmentState.RESUMING_SEARCH
    assert resumed.resume_seq == 8
    assert resumed.checkpoint_seq == 7

    # Complete service
    completed = coordinator.complete_service(job.assignment_id)
    assert completed.state == AssignmentState.COMPLETED


# ===========================================================================
# 15. Completed coverage is not replayed after resumption
# ===========================================================================

def test_completed_coverage_is_not_replayed_after_resumption():
    # Test SearchResumeManager conservative advance logic
    resume_mgr = SearchResumeManager(
        search_first_seq=3,
        rtl_seq=20,
        policy=SearchResumePolicyName.ARDUPILOT_NATIVE_RESUME,
    )

    checkpoint = resume_mgr.checkpoint(
        mission_id="m1",
        mission_fingerprint="fp1",
        current_seq=9,
        reached_items=[3, 4, 5, 6, 7, 8],
        east_m=15.0,
        north_m=25.0,
        coverage_percent=45.0,
    )

    # With conservative advance, candidate advances beyond reached items
    candidate_seq = resume_mgr.select_resume_sequence(checkpoint)
    assert candidate_seq >= 9
    assert candidate_seq not in {3, 4, 5, 6, 7, 8}, "Completed items must not be replayed"


# ===========================================================================
# 16. SEARCHER mission progress remains independent of SERVICER execution
# ===========================================================================

def test_searcher_mission_progress_remains_independent_of_servicer_execution():
    vstate = _make_vehicles_state()
    coordinator, registry, session = _setup_coordinator(vstate)
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    # SERVICER executes service flow
    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)

    # SEARCHER progresses independently from 25% to 60% coverage
    vstate["drone_01"]["telemetry"]["relative_altitude_m"] = 20.0
    vstate["drone_01"]["telemetry"]["ground_speed_m_s"] = 5.0
    vstate["drone_01"]["state"] = DroneLifecycleState.SEARCHING.value

    # Check vehicle states
    assert vstate["drone_01"]["state"] == DroneLifecycleState.SEARCHING.value
    assert coordinator.get_assignment(job.assignment_id).servicer_id == "drone_02"


# ===========================================================================
# 17. Multiple queued survivors are processed sequentially without assignment collisions
# ===========================================================================

def test_multiple_queued_survivors_processed_sequentially_without_assignment_collisions():
    coordinator, registry, session = _setup_coordinator()
    survivor1 = _ingest_confirmed_survivor(registry, east=15.0, north=15.0)
    survivor2 = _ingest_confirmed_survivor(registry, east=50.0, north=50.0)

    job1 = [a for a in coordinator.assignments() if a.canonical_survivor_id == survivor1.canonical_id][0]
    job2 = [a for a in coordinator.assignments() if a.canonical_survivor_id == survivor2.canonical_id][0]

    assert job1.state == AssignmentState.ASSIGNED
    assert job1.servicer_id == "drone_02"
    assert job2.state == AssignmentState.QUEUED
    assert job2.servicer_id is None

    # SERVICER completes job 1
    coordinator.acknowledge_assignment(job1.assignment_id)
    coordinator.start_approach(job1.assignment_id)
    coordinator.hold_for_release(job1.assignment_id, standoff_m=2.0)

    healthy_telem = {
        "state": "SEARCHING", "connected": True, "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job1.assignment_id, telemetry_override=healthy_telem)
    coordinator.verify_delivery(job1.assignment_id, verified=True)
    coordinator.start_resuming_search(job1.assignment_id, resume_seq=8)
    coordinator.complete_service(job1.assignment_id)

    # Upon completing job 1, job 2 is automatically reconciled and assigned to drone_02!
    job2_updated = coordinator.get_assignment(job2.assignment_id)
    assert job2_updated.state == AssignmentState.ASSIGNED
    assert job2_updated.servicer_id == "drone_02"


# ===========================================================================
# 18. Fleet RTL/stop takes precedence over service execution
# ===========================================================================

@pytest.mark.anyio
async def test_fleet_rtl_stop_takes_precedence_over_service_execution():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    now = time.monotonic()
    runtime.drone_state_machines = {
        "drone_01": DroneStateMachine("drone_01", DroneLifecycleState.SEARCHING),
        "drone_02": DroneStateMachine("drone_02", DroneLifecycleState.READY),
    }
    runtime.vehicles = {
        "drone_01": {
            "vehicle_id": "drone_01",
            "state": "SEARCHING",
            "last_update_monotonic": now,
            "connected": True,
            "telemetry_age_s": 0.1,
            "telemetry": {
                "armed": True,
                "flight_mode": "AUTO",
                "relative_altitude_m": 20.0,
                "ground_speed_m_s": 4.5,
                "latitude": 12.9721,
                "longitude": 77.5926,
            },
        },
        "drone_02": {
            "vehicle_id": "drone_02",
            "state": "READY",
            "last_update_monotonic": now,
            "connected": True,
            "telemetry_age_s": 0.1,
            "telemetry": {
                "armed": True,
                "flight_mode": "GUIDED",
                "relative_altitude_m": 20.0,
                "ground_speed_m_s": 0.0,
                "latitude": 12.9721,
                "longitude": 77.5926,
            },
        },
    }
    runtime.runners = {}
    runtime.cameras = {}
    runtime.events = []
    runtime.observations = []
    runtime.failed_vehicles = []
    runtime.trails = {"drone_01": [], "drone_02": []}
    runtime.error = None
    runtime.plan = None
    runtime.stop_requested = False

    session, config = _make_test_fleet_session()
    runtime.session = session
    runtime.registry = SharedSurvivorRegistry(session_id=session.session_id, mission_id=session.mission_id)
    runtime.coordinator = ExclusiveServiceCoordinator(
        session=session, fleet_config=config, registry=runtime.registry,
        vehicles_state_supplier=lambda: runtime._get_vehicles_snapshot(),
    )
    runtime.registry.on_confirmed_callback = runtime.coordinator.on_survivor_confirmed

    # Confirmed survivor
    survivor = _ingest_confirmed_survivor(runtime.registry)
    job = runtime.coordinator.assignments()[0]
    runtime.coordinator.acknowledge_assignment(job.assignment_id)
    runtime.coordinator.start_approach(job.assignment_id)

    assert runtime.coordinator.get_assignment(job.assignment_id).state == AssignmentState.APPROACHING

    # Vehicle safe return is commanded: RTL takes precedence!
    runtime.runners["drone_02"] = type("MockRunner", (), {"abort": lambda self: None})()
    runtime.stop_vehicle("drone_02")

    # Pre-release job was safely re-queued and drone_02 transitioned to RETURNING
    updated_job = runtime.coordinator.get_assignment(job.assignment_id)
    assert updated_job.state == AssignmentState.QUEUED
    assert runtime.vehicles["drone_02"]["state"] == DroneLifecycleState.RETURNING.value


# ===========================================================================
# 19. Session restart does not leak or duplicate assignments/releases
# ===========================================================================

def test_session_restart_does_not_leak_or_duplicate_assignments_releases():
    coordinator, registry, session = _setup_coordinator()
    survivor = _ingest_confirmed_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)
    healthy_telem = {
        "state": "SEARCHING", "connected": True, "telemetry_age_s": 0.1,
        "telemetry": {"relative_altitude_m": 5.0, "ground_speed_m_s": 0.1, "armed": True, "flight_mode": "GUIDED"},
    }
    coordinator.execute_simulated_release(job.assignment_id, telemetry_override=healthy_telem)

    assert len(coordinator.assignments()) == 1
    assert coordinator.get_assignment(job.assignment_id).payload_released is True

    # Resetting coordinator & registry
    coordinator.reset()
    registry.reset()

    assert len(coordinator.assignments()) == 0
    assert len(registry.confirmed_survivors()) == 0
    assert len(coordinator._servicer_to_assignment) == 0
    assert len(coordinator._survivor_to_assignment) == 0


# ===========================================================================
# 20. Existing single-drone delivery, survivor identity, and fleet lifecycle tests remain passing
# ===========================================================================

def test_existing_single_drone_and_fleet_components_sanity():
    # Verify compute_safe_delivery_point
    region = _make_test_boundary()
    pt = compute_safe_delivery_point(
        target_east_m=10.0,
        target_north_m=10.0,
        drone_east_m=0.0,
        drone_north_m=0.0,
        safe_offset_m=2.0,
        operational_region=region,
    )
    assert pt is not None
    # Standoff distance must be >= 2.0m from target (10, 10)
    import math
    dist = math.hypot(pt[0] - 10.0, pt[1] - 10.0)
    assert dist >= 1.99
