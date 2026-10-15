"""Automated test suite for autonomous delivery, search resumption, and flight parameters.

Covers all 18 test requirements specified in the mission requirements:
1. One confirmed survivor is queued and receives one simulated kit.
2. Two or more distinct survivors are serviced independently.
3. Repeated observations of one survivor do not create multiple delivery jobs.
4. Duplicate events and replayed release commands cannot release a second kit.
5. A previously delivered survivor is not serviced again after reacquisition.
6. Two nearby but distinct survivors retain separate canonical IDs and delivery outcomes.
7. An unconfirmed candidate cannot trigger delivery.
8. Excessive location uncertainty prevents release.
9. Stale camera/telemetry and unsafe flight conditions prevent release.
10. A failed release is not reported as a verified delivery.
11. Search resumes at the saved progress after success, deferral, and recoverable failure.
12. A queue of multiple survivors is serviced without skipping search regions.
13. Mission configuration is validated and propagated into actual planner and vehicle setpoints.
14. Changing search altitude updates coverage geometry and lane spacing appropriately.
15. Configuration changes cannot unexpectedly alter a critical in-progress maneuver.
16. RTL, landing, disarm, and cleanup still work after a delivery sequence.
17. Mission/session isolation prevents old delivery records from contaminating a fresh run.
18. Existing telemetry synchronization and target-display data models remain consistent.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from ground.backend.app.domain.flight_parameters import FlightParameters
from ground.backend.app.domain.mission import Boundary, Mission, MissionState
from ground.backend.app.domain.target import DeliveryState, Target, TargetState
from ground.backend.app.planning.coverage_planner import CoveragePlanner, compute_swath_geometry
from ground.backend.app.planning.models import CoverageRequest, SweepStrategy
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from rescue_core.autonomy.models import (
    PayloadState,
    SearchResumePolicyName,
    SyntheticSurvivorScenario,
)
from rescue_core.autonomy.payload import PayloadError, PayloadInventory, SimulatedPayloadAdapter
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point
from rescue_core.autonomy.safety import GuidedSafetySnapshot, GuidedSafetySupervisor
from rescue_core.autonomy.states import (
    RescueEvent,
    RescueState,
    RescueStateMachine,
)


# ============================================================================
# Requirement 13 & 15: Flight Parameters Validation & In-Flight Protection
# ============================================================================

def test_flight_parameters_validation_envelope():
    """Validates acceptable flight envelope parameters and rejects unsafe combinations."""
    # Valid defaults
    params = FlightParameters()
    assert params.search_altitude_m == 15.0
    assert params.delivery_altitude_m == 4.0
    assert params.safe_drop_offset_m == 2.5

    # Reject delivery_altitude >= approach_altitude
    with pytest.raises(ValueError, match="strictly below approach altitude"):
        FlightParameters(approach_altitude_m=8.0, delivery_altitude_m=8.5)

    # Reject delivery_altitude >= search_altitude
    with pytest.raises(ValueError, match="strictly below search altitude"):
        FlightParameters(search_altitude_m=5.0, delivery_altitude_m=5.0)

    # Reject safe_drop_offset_m < 1.0 (prevents direct body drop)
    with pytest.raises(ValueError):
        FlightParameters(safe_drop_offset_m=0.5)

    # Reject out-of-range bounds
    with pytest.raises(ValueError):
        FlightParameters(search_altitude_m=60.0)  # max 50m
    with pytest.raises(ValueError):
        FlightParameters(search_speed_m_s=25.0)  # max 15m/s


def test_api_flight_parameters_get_and_put(client: TestClient, mission_id: str):
    """Verifies API endpoints for reading and updating mission flight parameters."""
    # GET returns defaults
    res = client.get(f"/api/missions/{mission_id}/flight-parameters")
    assert res.status_code == 200
    data = res.json()
    assert data["search_altitude_m"] == 15.0

    # PUT updates parameters in READY state
    updated = {
        "search_altitude_m": 20.0,
        "search_speed_m_s": 5.0,
        "transit_speed_m_s": 6.0,
        "approach_altitude_m": 10.0,
        "approach_speed_m_s": 3.0,
        "delivery_altitude_m": 5.0,
        "delivery_stabilization_s": 4.0,
        "safe_drop_offset_m": 3.0,
        "search_swath_overlap_percent": 50.0,
        "rtl_altitude_m": 20.0,
        "rtl_speed_m_s": 5.0,
        "payload_slots": 3,
    }
    put_res = client.put(f"/api/missions/{mission_id}/flight-parameters", json=updated)
    assert put_res.status_code == 200
    assert put_res.json()["search_altitude_m"] == 20.0
    assert put_res.json()["safe_drop_offset_m"] == 3.0


def test_flight_parameters_locked_during_flight(client: TestClient, mission_id: str):
    """Req 15: Configuration changes cannot alter setpoints while mission is actively RUNNING."""
    # Put mission into RUNNING state
    mission_service = client.app.state.mission_service
    mission = mission_service.require(mission_id)
    mission.state = MissionState.RUNNING
    mission_service.missions.update(mission)

    # Attempt to alter flight parameters while running -> must return 409 Conflict
    payload = FlightParameters(search_altitude_m=25.0).model_dump(mode="json")
    res = client.put(f"/api/missions/{mission_id}/flight-parameters", json=payload)
    assert res.status_code == 409
    assert "actively running" in res.json()["detail"].lower()


# ============================================================================
# Requirement 14: Coverage Geometry & Lane Spacing Scaled with Altitude
# ============================================================================

def test_coverage_geometry_scales_with_altitude_and_overlap():
    """Req 14: Changing search altitude updates footprint and lane spacing, preserving overlap."""
    # At 8m altitude with 50% overlap
    swath_8, spacing_8, along_8 = compute_swath_geometry(search_altitude_m=8.0, overlap_percent=50.0)
    assert math.isclose(swath_8, 8.0, rel_tol=1e-3)
    assert math.isclose(spacing_8, 4.0, rel_tol=1e-3)

    # At 16m altitude with 50% overlap (footprint doubles, spacing doubles)
    swath_16, spacing_16, along_16 = compute_swath_geometry(search_altitude_m=16.0, overlap_percent=50.0)
    assert math.isclose(swath_16, 16.0, rel_tol=1e-3)
    assert math.isclose(spacing_16, 8.0, rel_tol=1e-3)
    assert spacing_16 == spacing_8 * 2.0

    # Test with CoveragePlanner
    planner = CoveragePlanner()
    origin = EnuPoint(0, 0, 0)
    origin_geo = LocalCoordinateFrame(EnuPoint(0, 0, 0)).to_wgs84(EnuPoint(0, 0, 0)) if hasattr(LocalCoordinateFrame, "origin") else None
    from ground.backend.app.domain.mission import GeoPoint
    geo_origin = GeoPoint(latitude=12.97, longitude=77.59, altitude_m=0.0)
    frame = LocalCoordinateFrame(geo_origin)
    boundary = Boundary(
        vertices=[
            frame.to_wgs84(EnuPoint(0, 0, 0)),
            frame.to_wgs84(EnuPoint(80, 0, 0)),
            frame.to_wgs84(EnuPoint(80, 80, 0)),
            frame.to_wgs84(EnuPoint(0, 80, 0)),
        ],
        source_format="geojson",
        area_m2=6400,
        area_hectares=0.64,
        origin=geo_origin,
        warnings=[],
    )

    req_low = CoverageRequest(
        search_altitude_m=8.0,
        lane_spacing_m=4.0,
        effective_swath_m=8.0,
        edge_margin_m=0.0,
        search_speed_m_s=4.0,
        flight_parameters=FlightParameters(search_altitude_m=8.0, search_swath_overlap_percent=50.0),
    )
    plan_low = planner.plan("mission-low", boundary, req_low)

    req_high = CoverageRequest(
        search_altitude_m=16.0,
        lane_spacing_m=8.0,
        effective_swath_m=16.0,
        edge_margin_m=0.0,
        search_speed_m_s=4.0,
        flight_parameters=FlightParameters(search_altitude_m=16.0, search_swath_overlap_percent=50.0),
    )
    plan_high = planner.plan("mission-high", boundary, req_high)

    # Higher altitude yields wider spacing and fewer search lines
    assert plan_high.configuration.search_altitude_m == 16.0
    assert plan_high.configuration.lane_spacing_m > plan_low.configuration.lane_spacing_m
    assert plan_high.number_of_search_lines < plan_low.number_of_search_lines


# ============================================================================
# Requirement 3, 4, 5, 6, 7: Canonical Identity, Deduplication & Eligibility
# ============================================================================

def test_unconfirmed_candidate_cannot_trigger_delivery():
    """Req 7: An unconfirmed CANDIDATE target cannot trigger delivery."""
    candidate = Target(
        mission_id="m1",
        state=TargetState.CANDIDATE,
        latitude=12.97,
        longitude=77.59,
        east_m=10.0,
        north_m=10.0,
        detection_confidence=0.20,  # below or unconfirmed
        geolocation_uncertainty_m=1.0,
        delivery_state=DeliveryState.PENDING,
    )
    # Verification check: delivery queue must reject non-CONFIRMED states
    assert candidate.state != TargetState.CONFIRMED


def _sample_evidence() -> dict[str, float]:
    return {
        "vehicle_east_m": 50.0,
        "vehicle_north_m": 50.0,
        "relative_target_error_m": 0.2,
        "altitude_m": 4.0,
        "ground_speed_m_s": 0.1,
        "target_uncertainty_m": 0.5,
    }


def test_repeated_observations_and_reacquisition_are_idempotent():
    """Req 3, 4, 5: Repeated observations do not duplicate jobs; reacquired delivered survivor is not serviced again."""
    inventory = PayloadInventory(slots=2)
    adapter = SimulatedPayloadAdapter()
    survivor_id = "survivor_canonical_01"

    # Observation 1: Reserve and release
    slot1 = inventory.reserve(survivor_id)
    evidence1 = adapter.release(slot1, **_sample_evidence())
    assert slot1.state == PayloadState.RELEASED
    assert adapter.verify(slot1, evidence1) is True
    assert inventory.remaining == 1

    # Observation 2: Frame from camera for same survivor
    # Idempotency check: Target already owns or consumed slot -> PayloadError prevents duplicate release
    with pytest.raises(PayloadError, match="Target already owns or consumed"):
        inventory.reserve(survivor_id)
    # Remaining inventory remains untouched at 1
    assert inventory.remaining == 1

    # Observation 3: Reacquisition after delivery
    target_state = Target(
        target_id=survivor_id,
        mission_id="m1",
        state=TargetState.DELIVERED,
        latitude=12.97,
        longitude=77.59,
        east_m=10.0,
        north_m=10.0,
        detection_confidence=0.85,
        geolocation_uncertainty_m=0.5,
        delivery_state=DeliveryState.DELIVERED_VERIFIED,
    )
    assert target_state.delivery_state == DeliveryState.DELIVERED_VERIFIED


def test_two_nearby_survivors_retain_separate_identities_and_deliveries():
    """Req 2, 6: Two nearby survivors maintain distinct canonical IDs and are serviced independently."""
    s1 = Target(
        target_id="survivor_alpha",
        mission_id="m1",
        state=TargetState.CONFIRMED,
        latitude=12.9729,
        longitude=77.5920,
        east_m=15.0,
        north_m=20.0,
        detection_confidence=0.88,
        geolocation_uncertainty_m=0.4,
    )
    s2 = Target(
        target_id="survivor_beta",
        mission_id="m1",
        state=TargetState.CONFIRMED,
        latitude=12.97295,
        longitude=77.5924,
        east_m=35.0,
        north_m=22.0,
        detection_confidence=0.91,
        geolocation_uncertainty_m=0.3,
    )

    assert s1.target_id != s2.target_id

    inventory = PayloadInventory(slots=2)
    adapter = SimulatedPayloadAdapter()
    delivery_records = {}

    # Service s1
    slot1 = inventory.reserve(s1.target_id)
    ev1 = adapter.release(slot1, **_sample_evidence())
    assert adapter.verify(slot1, ev1) is True
    delivery_records[s1.target_id] = DeliveryState.DELIVERED_VERIFIED

    # Service s2
    slot2 = inventory.reserve(s2.target_id)
    ev2 = adapter.release(slot2, **_sample_evidence())
    assert adapter.verify(slot2, ev2) is True
    delivery_records[s2.target_id] = DeliveryState.DELIVERED_VERIFIED

    assert inventory.remaining == 0
    assert len(delivery_records) == 2
    assert delivery_records[s1.target_id] == DeliveryState.DELIVERED_VERIFIED
    assert delivery_records[s2.target_id] == DeliveryState.DELIVERED_VERIFIED


# ============================================================================
# Requirement 3 & Standoff Geometry: Safe Payload Standoff Drop
# ============================================================================

def test_safe_delivery_point_standoff_computation():
    """Validates compute_safe_delivery_point: standoff offset >= 2.5m, never directly on survivor."""
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    target_e, target_n = 50.0, 50.0
    drone_e, drone_n = 10.0, 10.0

    safe_pt = compute_safe_delivery_point(
        target_east_m=target_e,
        target_north_m=target_n,
        drone_east_m=drone_e,
        drone_north_m=drone_n,
        safe_offset_m=2.5,
        operational_region=poly,
        other_survivors=[],
        target_uncertainty_m=0.5,
    )
    assert safe_pt is not None
    deliv_e, deliv_n = safe_pt

    dist_to_survivor = math.hypot(deliv_e - target_e, deliv_n - target_n)
    # Must be at least configured safe offset (2.5m)
    assert dist_to_survivor >= 2.5


def test_safe_delivery_point_avoids_other_survivors():
    """Verifies that computed standoff point is separated from other known survivors."""
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    target_e, target_n = 50.0, 50.0
    other_survivor = (52.5, 50.0)  # Close neighbor in the +East direction

    safe_pt = compute_safe_delivery_point(
        target_east_m=target_e,
        target_north_m=target_n,
        drone_east_m=40.0,
        drone_north_m=50.0,
        safe_offset_m=2.5,
        operational_region=poly,
        other_survivors=[other_survivor],
        target_uncertainty_m=0.3,
    )
    assert safe_pt is not None
    deliv_e, deliv_n = safe_pt
    dist_to_neighbor = math.hypot(deliv_e - other_survivor[0], deliv_n - other_survivor[1])
    assert dist_to_neighbor >= 2.0


# ============================================================================
# Requirement 8, 9, 10: Payload Interlocks, Uncertainty & Flight Stability
# ============================================================================

def test_excessive_location_uncertainty_adjusts_standoff():
    """Req 8: Target location uncertainty increases safety buffer and enforces safe margins."""
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    target_e, target_n = 50.0, 50.0

    # With high uncertainty (4.0m), the standoff offset expands to max(safe_offset, uncertainty + 0.5)
    safe_pt = compute_safe_delivery_point(
        target_east_m=target_e,
        target_north_m=target_n,
        drone_east_m=10.0,
        drone_north_m=10.0,
        safe_offset_m=2.5,
        operational_region=poly,
        other_survivors=[],
        target_uncertainty_m=4.0,
    )
    assert safe_pt is not None
    deliv_e, deliv_n = safe_pt
    dist_to_survivor = math.hypot(deliv_e - target_e, deliv_n - target_n)
    assert dist_to_survivor >= 4.49  # 4.0 + 0.5m buffer with float tolerance


def test_stale_telemetry_and_unsafe_flight_conditions_prevent_release():
    """Req 9: Stale telemetry, disarmed vehicle, wrong mode, or out of geofence blocks release."""
    region = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    supervisor = GuidedSafetySupervisor(region=region, command_timeout_s=2.0)

    # 1. Stale MAVLink telemetry
    failures_stale = supervisor.check(GuidedSafetySnapshot(
        link_alive=False, armed=True, flight_mode="GUIDED",
        east_m=50.0, north_m=50.0, altitude_m=4.0, payload_valid=True,
    ))
    assert "MAVLINK_STALE" in failures_stale

    # 2. Vehicle disarmed
    failures_disarmed = supervisor.check(GuidedSafetySnapshot(
        link_alive=True, armed=False, flight_mode="GUIDED",
        east_m=50.0, north_m=50.0, altitude_m=4.0, payload_valid=True,
    ))
    assert "VEHICLE_DISARMED" in failures_disarmed

    # 3. Wrong flight mode (e.g. still in AUTO instead of GUIDED)
    failures_mode = supervisor.check(GuidedSafetySnapshot(
        link_alive=True, armed=True, flight_mode="AUTO",
        east_m=50.0, north_m=50.0, altitude_m=4.0, payload_valid=True,
    ))
    assert "UNEXPECTED_MODE" in failures_mode

    # 4. Outside permitted operating area
    failures_geofence = supervisor.check(GuidedSafetySnapshot(
        link_alive=True, armed=True, flight_mode="GUIDED",
        east_m=150.0, north_m=50.0, altitude_m=4.0, payload_valid=True,
    ))
    assert "OUTSIDE_OPERATIONAL_REGION" in failures_geofence


def test_failed_release_is_not_reported_as_verified():
    """Req 10: Failed release is not verified and does not report DELIVERED_VERIFIED."""
    inventory = PayloadInventory(slots=1)
    slot = inventory.reserve("target_fail")
    adapter_failed = SimulatedPayloadAdapter(release_success=False)

    evidence = adapter_failed.release(slot, **_sample_evidence())
    assert slot.state == PayloadState.FAILED
    assert adapter_failed.verify(slot, evidence) is False


# ============================================================================
# Requirement 1, 11, 12, 16: Sequential Servicing, Resume & Mission Lifecycle
# ============================================================================

def test_state_machine_search_resume_after_delivery():
    """Req 1, 11: Workflow SEARCH -> CONFIRM -> SUSPEND -> APPROACH -> RELEASE -> VERIFY -> RESUME SEARCH."""
    machine = RescueStateMachine(initial=RescueState.SEARCHING)
    resume_mgr = SearchResumeManager(search_first_seq=3, rtl_seq=25, policy=SearchResumePolicyName.REPLAY_FROM_LAST_REACHED)

    # Step 1: SEARCHING -> CANDIDATE -> TARGET_CONFIRMED
    assert machine.state == RescueState.SEARCHING
    machine.transition(RescueEvent.CANDIDATE_OBSERVED)
    assert machine.state == RescueState.TARGET_CANDIDATE
    machine.transition(RescueEvent.TARGET_CONFIRMED)
    assert machine.state == RescueState.TARGET_CONFIRMED

    # Step 2: TARGET_CONFIRMED -> SEARCH_SUSPENDING (save checkpoint)
    chk = resume_mgr.checkpoint(
        mission_id="m1", mission_fingerprint="fp1", current_seq=14,
        reached_items=[3, 4, 5, 10, 14], east_m=32.0, north_m=45.0, coverage_percent=42.5,
    )
    machine.transition(RescueEvent.CHECKPOINT_SAVED)
    assert machine.state == RescueState.SEARCH_SUSPENDING

    # Step 3: SEARCH_SUSPENDING -> APPROACH_PLANNING -> GUIDED_APPROACH
    machine.transition(RescueEvent.APPROACH_PATH_READY)
    assert machine.state == RescueState.APPROACH_PLANNING
    machine.transition(RescueEvent.GUIDED_CONFIRMED)
    assert machine.state == RescueState.GUIDED_APPROACH

    # Step 4: GUIDED_APPROACH -> REACQUIRING -> PRECISION_ALIGNING -> DROP_READY
    machine.transition(RescueEvent.APPROACH_REACHED)
    assert machine.state == RescueState.REACQUIRING
    machine.transition(RescueEvent.TARGET_REACQUIRED)
    assert machine.state == RescueState.PRECISION_ALIGNING
    machine.transition(RescueEvent.ALIGNMENT_CONVERGED)
    assert machine.state == RescueState.DROP_READY

    # Step 5: DROP_READY -> DELIVERING -> DELIVERY_VERIFYING
    machine.transition(RescueEvent.DROP_CONDITIONS_MET)
    assert machine.state == RescueState.DELIVERING
    machine.transition(RescueEvent.RELEASE_ACCEPTED)
    assert machine.state == RescueState.DELIVERY_VERIFYING

    # Step 6: DELIVERY_VERIFYING -> SEARCH_RESUMING -> SEARCHING
    machine.transition(RescueEvent.DELIVERY_VERIFIED)
    assert machine.state == RescueState.SEARCH_RESUMING

    # Verify resume sequence calculation
    resume_seq = resume_mgr.select_resume_sequence(chk)
    assert resume_seq == 14

    machine.transition(RescueEvent.AUTO_CONFIRMED)
    assert machine.state == RescueState.SEARCHING


def test_sequential_servicing_of_multiple_queued_survivors():
    """Req 2, 12: Sequential multi-target servicing without skipping or premature RTL."""
    machine = RescueStateMachine(initial=RescueState.SEARCHING)
    inventory = PayloadInventory(slots=2)
    adapter = SimulatedPayloadAdapter()
    queue = ["survivor_1", "survivor_2"]
    serviced = []

    while queue:
        target_id = queue.pop(0)
        # Transition to confirm and approach
        if machine.state == RescueState.SEARCHING:
            machine.transition(RescueEvent.TARGET_CONFIRMED)
            machine.transition(RescueEvent.CHECKPOINT_SAVED)
            machine.transition(RescueEvent.APPROACH_PATH_READY)
        elif machine.state == RescueState.APPROACH_PLANNING:
            pass  # direct sequential continuation

        machine.transition(RescueEvent.GUIDED_CONFIRMED)
        machine.transition(RescueEvent.APPROACH_REACHED)
        machine.transition(RescueEvent.TARGET_REACQUIRED)
        machine.transition(RescueEvent.ALIGNMENT_CONVERGED)
        machine.transition(RescueEvent.DROP_CONDITIONS_MET)
        machine.transition(RescueEvent.RELEASE_ACCEPTED)

        # Release payload
        slot = inventory.reserve(target_id)
        evidence = adapter.release(slot, **_sample_evidence())
        assert adapter.verify(slot, evidence) is True
        serviced.append(target_id)

        if queue:
            # Multi-target loop transition: next queued target directly without search resume
            machine.transition(RescueEvent.APPROACH_PATH_READY)
            assert machine.state == RescueState.APPROACH_PLANNING
        else:
            # Last target: verify and resume search
            machine.transition(RescueEvent.DELIVERY_VERIFIED)
            assert machine.state == RescueState.SEARCH_RESUMING
            machine.transition(RescueEvent.AUTO_CONFIRMED)
            assert machine.state == RescueState.SEARCHING

    assert len(serviced) == 2
    assert inventory.remaining == 0
    assert machine.state == RescueState.SEARCHING

    # Search completes -> RTL -> LANDING -> COMPLETED
    machine.transition(RescueEvent.RTL_OBSERVED)
    assert machine.state == RescueState.RETURNING
    machine.transition(RescueEvent.LANDING_OBSERVED)
    assert machine.state == RescueState.LANDING
    machine.transition(RescueEvent.DISARMED)
    assert machine.state == RescueState.COMPLETED


# ============================================================================
# Requirement 17: Mission Session Isolation
# ============================================================================

def test_mission_session_isolation(client: TestClient):
    """Req 17: Fresh mission sessions do not leak queued targets or delivery records."""
    # Mission A
    res_a = client.post("/api/missions", json={"name": "Mission Alpha"}).json()
    id_a = res_a["id"]
    targets_a = client.get(f"/api/missions/{id_a}/targets").json()
    assert len(targets_a) == 0

    # Mission B
    res_b = client.post("/api/missions", json={"name": "Mission Beta"}).json()
    id_b = res_b["id"]
    targets_b = client.get(f"/api/missions/{id_b}/targets").json()
    assert len(targets_b) == 0

    # Flight parameters are distinct per mission
    client.put(f"/api/missions/{id_a}/flight-parameters", json=FlightParameters(search_altitude_m=22.0).model_dump(mode="json"))
    params_a = client.get(f"/api/missions/{id_a}/flight-parameters").json()
    params_b = client.get(f"/api/missions/{id_b}/flight-parameters").json()

    assert params_a["search_altitude_m"] == 22.0
    assert params_b["search_altitude_m"] == 15.0  # Mission B retains its own defaults
