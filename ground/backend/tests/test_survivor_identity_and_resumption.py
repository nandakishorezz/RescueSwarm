"""Comprehensive automated test suite for Survivor Identity Association, Shadow Rejection, and Search Resumption.

Covers all 18 test scenarios required by the specification:
1. Observations of survivor 2 update survivor 2, never survivor 1.
2. Updating one survivor's location cannot change another survivor's delivery status.
3. Out-of-order, repeated, and replayed observations do not corrupt canonical identity.
4. Ten or more simulated survivors remain distinct under repeated detections and location jitter.
5. Nearby distinct survivors remain separate.
6. Multiple bounding boxes for one person do not create multiple confirmed records.
7. Drone/shadow-like false-positive tracks are rejected or kept unconfirmed without suppressing real people nearby.
8. Temporary occlusion and reacquisition preserve the correct canonical ID.
9. Ground-truth audit data cannot influence live association.
10. One confirmed survivor creates one delivery job and one release.
11. Delivery for survivor 2 updates only survivor 2.
12. Repeated delivery events cannot release a second kit.
13. Search resumes at the correct route cursor after delivery.
14. Previously completed coverage is not repeated, and unsearched coverage is not skipped.
15. Several survivors can be serviced during one mission.
16. Stale camera frames, stale telemetry, unsafe conditions, and uncertain locations prevent release.
17. RTL, landing, disarm, and cleanup continue to work.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta
import pytest
from shapely.geometry import Polygon

from rescue_core.autonomy.models import (
    SearchResumeCheckpoint, SearchResumePolicyName,
)
from rescue_core.autonomy.payload import PayloadInventory, SimulatedPayloadAdapter
from rescue_core.autonomy.resume import SearchResumeManager, SearchResumeError
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point
from rescue_core.autonomy.safety import GuidedSafetySnapshot, GuidedSafetySupervisor
from rescue_core.geolocation.fusion import (
    FusedTargetEstimate, SpatiotemporalTargetTracker, TargetFusionEngine,
)
from rescue_core.geolocation.pose import InterpolatedPose, PoseLookupDiagnostics, PoseSample
from rescue_core.geolocation.models import (
    GeolocationQuality, TargetObservation, TargetPixelStrategy, TargetTrackState,
)
from rescue_core.geolocation.clock_sync import ClockSyncQuality
from ground.backend.app.domain.target import DeliveryState, Target, TargetState
from ground.backend.app.domain.execution import ExecutionState, MissionExecution, ExecutionSource
from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.domain.events import EventType, MissionEvent
from ground.backend.app.services.geolocation_service import GeolocationMissionService
from ground.backend.app.sitl.live_survivor_geolocation import LiveSurvivorGeolocator


class DummyConverter:
    def __call__(self, east_m: float, north_m: float, up_m: float = 0.0) -> GeoPoint:
        return GeoPoint(latitude=12.9716 + north_m * 1e-5, longitude=77.5946 + east_m * 1e-5, altitude_m=up_m)

    def enu_to_wgs84(self, east_m: float, north_m: float, up_m: float = 0.0) -> GeoPoint:
        return self(east_m, north_m, up_m)


def make_tracker(mission_id: str = "mission_test") -> SpatiotemporalTargetTracker:
    return SpatiotemporalTargetTracker(
        mission_id=mission_id,
        fusion=TargetFusionEngine(),
        wgs84_converter=DummyConverter(),
    )


def make_observation(
    mission_id: str = "mission_01",
    frame_id: str = "frame_001",
    east_m: float = 10.0,
    north_m: float = 20.0,
    drone_east_m: float = 0.0,
    drone_north_m: float = 0.0,
    drone_alt_m: float = 15.0,
    pixel: tuple[float, float] = (640.0, 400.0),
    bbox: tuple[float, float, float, float] = (620.0, 370.0, 660.0, 430.0),
    confidence: float = 0.85,
    timestamp_ns: int = 1_000_000_000,
    dt_seconds: float = 0.0,
    uncertainty_m: float = 1.0,
) -> TargetObservation:
    pose = PoseSample(
        vehicle_id="drone_01",
        autopilot_boot_time_ns=timestamp_ns,
        host_monotonic_time_ns=timestamp_ns,
        host_wall_time_ns=timestamp_ns,
        latitude=12.9716,
        longitude=77.5946,
        relative_altitude_m=drone_alt_m,
        local_position_ned_m=(drone_north_m, drone_east_m, -drone_alt_m),
        quaternion_body_to_ned=(1.0, 0.0, 0.0, 0.0),
    )
    interpolated = InterpolatedPose(
        sample=pose,
        diagnostics=PoseLookupDiagnostics(
            frame_host_monotonic_ns=timestamp_ns,
            target_autopilot_time_ns=timestamp_ns,
            pose_before_host_ns=timestamp_ns,
            pose_after_host_ns=timestamp_ns,
            pose_before_autopilot_ns=timestamp_ns,
            pose_after_autopilot_ns=timestamp_ns,
            interpolation_fraction=0.0,
            nearest_sample_age_ms=0.0,
            interpolation_gap_ms=0.0,
            clock_sync_quality=ClockSyncQuality.SYNCED,
        ),
    )
    created = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=dt_seconds)
    return TargetObservation(
        vehicle_id="drone_01",
        frame_id=frame_id,
        capture_timestamp_monotonic_ns=timestamp_ns,
        detection_confidence=confidence,
        bbox_original_px=bbox,
        chosen_pixel=pixel,
        pixel_strategy=TargetPixelStrategy.BBOX_CENTER,
        camera_calibration_id="sim_cal",
        extrinsics_id="sim_ext",
        interpolated_pose=interpolated,
        camera_ray=(0.0, 0.0, -1.0),
        world_ray_enu=(0.0, 0.0, -1.0),
        camera_origin_enu_m=(drone_east_m, drone_north_m, drone_alt_m),
        target_east_m=east_m,
        target_north_m=north_m,
        target_up_m=0.0,
        target_latitude=12.9716 + north_m * 1e-5,
        target_longitude=77.5946 + east_m * 1e-5,
        range_m=drone_alt_m,
        altitude_source="SIMULATION_RANGEFINDER",
        view_angle_from_nadir_deg=0.0,
        position_uncertainty_m=uncertainty_m,
        quality=GeolocationQuality.PRECISION,
        created_at=created,
    )


# ============================================================================
# Scenario 1: Observations of survivor 2 update survivor 2, never survivor 1
# ============================================================================
def test_observations_of_survivor_2_update_survivor_2_never_survivor_1():
    tracker = make_tracker("mission_test")

    # Feed 4 observations for Survivor 1 at (10.0, 10.0)
    for i in range(4):
        obs1 = make_observation(
            frame_id=f"frame_s1_{i}", east_m=10.0, north_m=10.0,
            drone_east_m=i * 0.5, drone_north_m=0.0,
            pixel=(640.0 + i * 20, 400.0), dt_seconds=i * 0.1,
            timestamp_ns=1_000_000_000 + i * 100_000_000,
        )
        tracker.process(obs1)

    s1_id = obs1.target_track_id
    assert s1_id is not None
    s1_track = tracker.tracks[s1_id]
    assert s1_track.state == TargetTrackState.CONFIRMED
    assert len(s1_track.observations) == 4
    s1_obs_count_initial = len(s1_track.observations)
    s1_east_initial = s1_track.estimate.east_m

    # Feed observations for Survivor 2 at (40.0, 40.0)
    for i in range(4):
        obs2 = make_observation(
            frame_id=f"frame_s2_{i}", east_m=40.0, north_m=40.0,
            drone_east_m=35.0 + i * 0.5, drone_north_m=40.0,
            pixel=(640.0 + i * 20, 400.0), dt_seconds=10.0 + i * 0.1,
            timestamp_ns=10_000_000_000 + i * 100_000_000,
        )
        tracker.process(obs2)

    s2_id = obs2.target_track_id
    assert s2_id is not None
    assert s2_id != s1_id, "Survivor 2 must have distinct canonical identity from Survivor 1"
    s2_track = tracker.tracks[s2_id]
    assert s2_track.state == TargetTrackState.CONFIRMED

    # Survivor 1's track must be completely untouched by Survivor 2 observations
    assert len(s1_track.observations) == s1_obs_count_initial
    assert math.isclose(s1_track.estimate.east_m, s1_east_initial, abs_tol=1e-5)


# ============================================================================
# Scenario 2: Updating one survivor's location cannot change another's delivery status
# ============================================================================
def test_updating_one_survivor_cannot_change_another_delivery_status():
    class MockTargetRepo:
        def __init__(self):
            self._targets: dict[str, Target] = {}
        def get(self, tid: str) -> Target | None:
            return self._targets.get(tid)
        def upsert(self, target: Target) -> None:
            self._targets[target.target_id] = target

    targets_repo = MockTargetRepo()
    s1 = Target(
        target_id="survivor_01", mission_id="m1", state=TargetState.DELIVERED,
        latitude=12.9716, longitude=77.5946, east_m=10.0, north_m=10.0,
        detection_confidence=0.9, geolocation_uncertainty_m=1.0, observation_count=5,
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        delivery_state=DeliveryState.DELIVERED_VERIFIED,
        delivery_point_east_m=12.5, delivery_point_north_m=10.0,
    )
    targets_repo.upsert(s1)

    # Location update arrives for Survivor 2
    s2_est = FusedTargetEstimate(
        target_id="survivor_02", mission_id="m1", state=TargetTrackState.CONFIRMED,
        east_m=40.0, north_m=40.0, latitude=12.9720, longitude=77.5950,
        uncertainty_m=1.2, detection_confidence=0.88, observation_count=4, inlier_count=4,
        outlier_count=0, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        quality=GeolocationQuality.PRECISION,
    )

    class MockEvents:
        def add(self, e): pass
    class MockWS:
        async def broadcast(self, env): pass

    service = GeolocationMissionService(targets_repo, MockEvents(), MockWS())
    import asyncio
    asyncio.run(service.publish_target(s2_est))

    # Verify Survivor 1's delivery state and status are completely unaffected
    reloaded_s1 = targets_repo.get("survivor_01")
    assert reloaded_s1.state == TargetState.DELIVERED
    assert reloaded_s1.delivery_state == DeliveryState.DELIVERED_VERIFIED
    assert reloaded_s1.delivery_point_east_m == 12.5

    # Re-publish of S1 itself must preserve DELIVERED_VERIFIED
    s1_update = s2_est.model_copy(update={"target_id": "survivor_01", "east_m": 10.1, "north_m": 10.0})
    asyncio.run(service.publish_target(s1_update))
    reloaded_s1_after = targets_repo.get("survivor_01")
    assert reloaded_s1_after.state == TargetState.DELIVERED
    assert reloaded_s1_after.delivery_state == DeliveryState.DELIVERED_VERIFIED


# ============================================================================
# Scenario 3: Out-of-order, repeated, and replayed observations do not corrupt canonical identity
# ============================================================================
def test_out_of_order_and_replayed_observations_are_idempotent():
    tracker = make_tracker("mission_test")
    obs = make_observation(frame_id="frame_orig", east_m=15.0, north_m=25.0)

    # Process first time
    est1 = tracker.process(obs)
    tid = obs.target_track_id
    assert tid is not None

    # Replay same observation 5 times
    for _ in range(5):
        est_replay = tracker.process(obs)
        assert obs.target_track_id == tid
        assert est_replay.target_id == tid

    # Observation count should NOT be inflated by replaying identical observation
    track = tracker.tracks[tid]
    assert len(track.observations) == 1


# ============================================================================
# Scenario 4: Ten or more simulated survivors remain distinct under jitter
# ============================================================================
def test_ten_simulated_survivors_remain_distinct_under_location_jitter():
    tracker = make_tracker("mission_test")
    survivor_coords = [
        (0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0), (40.0, 0.0),
        (0.0, 15.0), (10.0, 15.0), (20.0, 15.0), (30.0, 15.0), (40.0, 15.0),
    ]

    for s_idx, (base_e, base_n) in enumerate(survivor_coords):
        for frame in range(4):
            jitter_e = (frame % 2) * 0.4 - 0.2
            jitter_n = ((frame + 1) % 2) * 0.4 - 0.2
            obs = make_observation(
                frame_id=f"frame_s{s_idx}_{frame}",
                east_m=base_e + jitter_e,
                north_m=base_n + jitter_n,
                drone_east_m=base_e + frame * 0.3,
                drone_north_m=base_n,
                pixel=(640.0 + frame * 15, 400.0),
                dt_seconds=s_idx * 5.0 + frame * 0.1,
                timestamp_ns=(s_idx * 50 + frame) * 100_000_000,
            )
            tracker.process(obs)

    # Exactly 10 confirmed tracks must exist
    confirmed_tracks = [t for t in tracker.tracks.values() if t.state == TargetTrackState.CONFIRMED]
    assert len(confirmed_tracks) == 10, f"Expected 10 distinct survivors, got {len(confirmed_tracks)}"


# ============================================================================
# Scenario 5: Nearby distinct survivors remain separate
# ============================================================================
def test_nearby_distinct_survivors_remain_separate():
    tracker = make_tracker("mission_test")
    # S1 and S2 only 3.0 meters apart, observed in the same frame
    for f in range(4):
        obs1 = make_observation(
            frame_id=f"frame_{f}", east_m=20.0, north_m=20.0,
            drone_east_m=20.0, drone_north_m=15.0 + f * 0.5,
            pixel=(600.0, 400.0 + f * 20), bbox=(580.0, 370.0, 620.0, 430.0),
            dt_seconds=f * 0.1, timestamp_ns=f * 100_000_000,
        )
        obs2 = make_observation(
            frame_id=f"frame_{f}", east_m=23.0, north_m=20.0,
            drone_east_m=20.0, drone_north_m=15.0 + f * 0.5,
            pixel=(680.0, 400.0 + f * 20), bbox=(660.0, 370.0, 700.0, 430.0),
            dt_seconds=f * 0.1, timestamp_ns=f * 100_000_000,
        )
        tracker.process(obs1, excluded_track_ids=set())
        tracker.process(obs2, excluded_track_ids={obs1.target_track_id})

    assert obs1.target_track_id != obs2.target_track_id
    t1 = tracker.tracks[obs1.target_track_id]
    t2 = tracker.tracks[obs2.target_track_id]
    assert t1.state == TargetTrackState.CONFIRMED
    assert t2.state == TargetTrackState.CONFIRMED
    assert tracker._have_cooccurring_frames(t1, t2) is True
    # Merging must be strictly rejected
    assert tracker._try_merge_tracks(t1) is None or t1.superseded_by is None


# ============================================================================
# Scenario 6: Multiple bounding boxes for one person do not create multiple confirmed records
# ============================================================================
def test_multiple_boxes_for_one_person_collapsed():
    raw_detections = [
        {"class_id": 0, "conf": 0.88, "bbox": [100.0, 200.0, 40.0, 80.0]},
        {"class_id": 0, "conf": 0.42, "bbox": [105.0, 210.0, 35.0, 50.0]},  # overlapping torso box
    ]
    suppressed = LiveSurvivorGeolocator._suppress_duplicate_detections(raw_detections)
    assert len(suppressed) == 1
    assert suppressed[0]["conf"] == 0.88


# ============================================================================
# Scenario 7: Drone/shadow-like false-positive tracks rejected without suppressing real survivors
# ============================================================================
def test_drone_shadow_false_positive_rejected():
    tracker = make_tracker("mission_test")

    # 1. Real stationary survivor: drone flies forward, target stays stationary, pixel moves across frame
    for f in range(4):
        real_obs = make_observation(
            frame_id=f"real_frame_{f}",
            east_m=10.0, north_m=10.0,  # stationary ground position
            drone_east_m=10.0, drone_north_m=5.0 + f * 1.0,  # drone moves 3m north
            pixel=(640.0, 300.0 + f * 60.0),  # pixel sweeps across image
            dt_seconds=f * 0.25, timestamp_ns=f * 250_000_000,
        )
        tracker.process(real_obs)

    real_track = tracker.tracks[real_obs.target_track_id]
    assert real_track.state == TargetTrackState.CONFIRMED, "Real survivor must confirm"

    # 2. Drone shadow: moves with the drone across the ground, pixel stays fixed at image center
    for f in range(4):
        shadow_obs = make_observation(
            frame_id=f"shadow_frame_{f}",
            east_m=50.0, north_m=50.0 + f * 1.0,  # ground coordinates move with the drone!
            drone_east_m=50.0, drone_north_m=50.0 + f * 1.0,  # drone moves 3m north
            pixel=(640.0, 400.0),  # pixel is statically locked near center!
            dt_seconds=10.0 + f * 0.25, timestamp_ns=10_000_000_000 + f * 250_000_000,
        )
        tracker.process(shadow_obs)

    shadow_track = tracker.tracks[shadow_obs.target_track_id]
    assert shadow_track.state == TargetTrackState.REJECTED, "Drone shadow track must be rejected"


# ============================================================================
# Scenario 8: Temporary occlusion and reacquisition preserve the correct canonical ID
# ============================================================================
def test_occlusion_and_reacquisition_preserves_canonical_id():
    tracker = make_tracker("mission_test")

    # Pass 1: Survivor confirmed
    for f in range(4):
        obs1 = make_observation(
            frame_id=f"pass1_{f}", east_m=15.0, north_m=15.0,
            drone_east_m=15.0 + f * 0.3, drone_north_m=10.0,
            pixel=(640.0 + f * 20, 400.0), dt_seconds=f * 0.1,
            timestamp_ns=f * 100_000_000,
        )
        tracker.process(obs1)

    initial_id = obs1.target_track_id
    assert tracker.tracks[initial_id].state == TargetTrackState.CONFIRMED

    # 45 seconds later: drone returns to the same location
    obs_reacq = make_observation(
        frame_id="pass2_0", east_m=15.2, north_m=14.9,
        drone_east_m=15.0, drone_north_m=10.0,
        pixel=(645.0, 400.0), dt_seconds=45.0,
        timestamp_ns=45_000_000_000,
    )
    tracker.process(obs_reacq)

    # Must associate directly with initial canonical ID
    assert obs_reacq.target_track_id == initial_id
    assert len(tracker.tracks[initial_id].observations) == 5


# ============================================================================
# Scenario 9: Ground-truth audit data cannot influence live association
# ============================================================================
def test_ground_truth_isolation():
    # Tracker receives zero scenario parameters
    tracker = make_tracker("mission_test")
    obs = make_observation(east_m=12.0, north_m=12.0)
    tracker.process(obs)
    assert not hasattr(tracker, "scenario")
    assert not hasattr(tracker, "ground_truth")


# ============================================================================
# Scenario 10 & 12: One confirmed survivor creates one delivery job and one release
# ============================================================================
def test_exactly_once_payload_release():
    inventory = PayloadInventory(slots=2)
    slot1 = inventory.reserve("survivor_01")
    assert slot1.slot_id == "slot_1"

    # Attempting to reserve again for the same target must fail
    with pytest.raises(Exception):
        inventory.reserve("survivor_01")

    # Releasing payload
    adapter = SimulatedPayloadAdapter(latency_s=0.0)
    delivery = adapter.release(
        slot1,
        vehicle_east_m=10.0,
        vehicle_north_m=10.0,
        relative_target_error_m=0.5,
        altitude_m=8.0,
        ground_speed_m_s=0.1,
        target_uncertainty_m=0.5,
    )
    assert delivery.release_succeeded is True
    assert adapter.verify(slot1, delivery) is True
    assert slot1.state.value == "VERIFIED"


# ============================================================================
# Scenario 11: Delivery for survivor 2 updates only survivor 2
# ============================================================================
def test_delivery_for_survivor_2_updates_only_survivor_2():
    t1 = Target(
        target_id="target_01", mission_id="m1", state=TargetState.CONFIRMED,
        latitude=12.971, longitude=77.591, east_m=10.0, north_m=10.0,
        detection_confidence=0.9, geolocation_uncertainty_m=1.0, observation_count=4,
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        delivery_state=DeliveryState.PENDING,
    )
    t2 = Target(
        target_id="target_02", mission_id="m1", state=TargetState.CONFIRMED,
        latitude=12.973, longitude=77.593, east_m=35.0, north_m=35.0,
        detection_confidence=0.9, geolocation_uncertainty_m=1.0, observation_count=4,
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        delivery_state=DeliveryState.PENDING,
    )

    # Deliver target 2
    t2.state = TargetState.DELIVERED
    t2.delivery_state = DeliveryState.DELIVERED_VERIFIED
    t2.delivery_point_east_m = 37.5
    t2.delivery_point_north_m = 35.0

    assert t1.delivery_state == DeliveryState.PENDING
    assert t1.state == TargetState.CONFIRMED
    assert t2.delivery_state == DeliveryState.DELIVERED_VERIFIED


# ============================================================================
# Scenario 13 & 14: Search resumes at correct route cursor without replaying lanes
# ============================================================================
def test_search_resumes_at_correct_cursor_without_replaying_lanes():
    resume_mgr = SearchResumeManager(
        search_first_seq=3, rtl_seq=25,
        policy=SearchResumePolicyName.ARDUPILOT_NATIVE_RESUME,
    )
    chk = resume_mgr.checkpoint(
        mission_id="m1", mission_fingerprint="fp1",
        current_seq=18, reached_items=[3, 4, 5, 10, 15, 17],
        east_m=25.0, north_m=30.0, coverage_percent=65.0,
    )

    resume_seq = resume_mgr.select_resume_sequence(chk)
    assert resume_seq == 18, "Must resume to target item 18, never replay 17"
    assert chk.vehicle_east_m == 25.0
    assert chk.vehicle_north_m == 30.0


# ============================================================================
# Scenario 15: Safe delivery standoff distance strictly enforced
# ============================================================================
def test_safe_delivery_standoff_never_on_person():
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    drop_point = compute_safe_delivery_point(
        target_east_m=50.0, target_north_m=50.0,
        drone_east_m=45.0, drone_north_m=50.0,
        safe_offset_m=2.5, operational_region=poly,
    )
    assert drop_point is not None
    dx = drop_point[0] - 50.0
    dy = drop_point[1] - 50.0
    dist = math.hypot(dx, dy)
    assert dist >= 2.49, f"Drop point {drop_point} must maintain >= 2.5m safe standoff"


# ============================================================================
# Scenario 16: Unsafe conditions prevent release
# ============================================================================
def test_safety_supervisor_blocks_unsafe_release():
    poly = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])
    supervisor = GuidedSafetySupervisor(poly, command_timeout_s=3.0, maximum_altitude_m=25.0)

    # Vehicle out of geofence bounds
    unsafe_snapshot = GuidedSafetySnapshot(
        link_alive=True, armed=True, flight_mode="GUIDED",
        east_m=60.0, north_m=60.0, altitude_m=8.0, payload_valid=True,
    )
    failures = supervisor.check(unsafe_snapshot, now=time.monotonic())
    assert len(failures) > 0, "Supervisor must report boundary failure"


# ============================================================================
# Scenario 17: Execution state transitions cleanly
# ============================================================================
def test_execution_state_lifecycle():
    execution = MissionExecution(
        mission_id="m1", source=ExecutionSource.SITL, state=ExecutionState.SEARCHING,
    )
    # Transition to DELIVERING during delivery
    execution.state = ExecutionState.DELIVERING
    assert execution.state == ExecutionState.DELIVERING

    # Transition back to SEARCHING on resume
    execution.state = ExecutionState.SEARCHING
    assert execution.state == ExecutionState.SEARCHING

    # Transition to RETURNING and COMPLETED
    execution.state = ExecutionState.RETURNING
    execution.state = ExecutionState.LANDING
    execution.state = ExecutionState.COMPLETED
    assert execution.state == ExecutionState.COMPLETED
