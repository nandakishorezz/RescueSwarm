from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pytest

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.domain.vehicle import VehicleState
from ground.backend.app.sitl.live_survivor_geolocation import LiveSurvivorGeolocator
from rescue_core.geolocation.pose import PoseSample
from rescue_core.geolocation.projection import SyntheticTargetProjector
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.simulation.perception.pipeline import simulation_camera_geometry
from rescue_core.simulation.profiles import load_camera_profile


ROOT = Path(__file__).resolve().parents[3]
ORIGIN = GeoPoint(latitude=12.9716, longitude=77.5946, altitude_m=0.0)


def _locator() -> LiveSurvivorGeolocator:
    profile = load_camera_profile(ROOT / "simulation/profiles/ov9281_reference_simulation.yaml")
    return LiveSurvivorGeolocator("mission-live", ORIGIN, profile)


def _vehicle(altitude_m: float = 20.0) -> VehicleState:
    return VehicleState(
        vehicle_id="drone_01", name="SITL", connected=True, latitude=ORIGIN.latitude,
        longitude=ORIGIN.longitude, relative_altitude_m=altitude_m, absolute_altitude_m=altitude_m,
        gps_fix_type=3, satellites=12, quaternion_body_to_ned=(1.0, 0.0, 0.0, 0.0),
        simulated=True,
    )


def _box_for_target(locator: LiveSurvivorGeolocator, east: float, north: float) -> list[float]:
    calibration, extrinsics = simulation_camera_geometry(locator.camera)
    pose = PoseSample(
        vehicle_id="drone_01", autopilot_boot_time_ns=1, host_monotonic_time_ns=1, host_wall_time_ns=1,
        latitude=ORIGIN.latitude, longitude=ORIGIN.longitude, absolute_altitude_m=20.0,
        relative_altitude_m=20.0, local_position_ned_m=(0.0, 0.0, -20.0),
        quaternion_body_to_ned=(1.0, 0.0, 0.0, 0.0), gps_fix=3,
    )
    pixel = SyntheticTargetProjector(CameraModel.from_calibration(calibration), extrinsics).project(
        np.array([east, north, 0.0]), pose,
    )
    assert pixel is not None
    return [pixel[0] - 2, pixel[1] - 4, 4, 4]


def _detection(box: list[float]) -> dict[str, object]:
    return {"class_id": 0, "class": "item", "conf": 0.9, "bbox": box}


def test_projects_known_pose_and_pixel_to_ground_enu() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    output = locator.observe(
        frame_id="camera:1", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(locator, 4.0, 3.0))], width=1280, height=800,
    )[0]
    assert output.observation is not None, output.reason
    assert output.observation.target_east_m == pytest.approx(4.0, abs=0.1)
    assert output.observation.target_north_m == pytest.approx(3.0, abs=0.1)
    assert output.observation.position_uncertainty_m > 0


def test_rejects_mismatched_camera_geometry_without_coordinates() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    output = locator.observe(
        frame_id="camera:1", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection([600, 400, 20, 20])], width=640, height=400,
    )[0]
    assert output.observation is None
    assert "CALIBRATION" in (output.reason or "")


def test_ground_level_boxes_do_not_create_geolocated_candidate_tracks() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle(altitude_m=0.1))
    output = locator.observe(
        frame_id="camera:ground", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection([600, 400, 20, 20])], width=1280, height=800,
    )[0]

    assert output.observation is None
    assert output.reason == "ALTITUDE_TOO_LOW_FOR_GEOLOCATION"
    assert locator.tracker.estimates() == []


def test_live_full_height_model_box_is_not_registration_evidence() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle(altitude_m=8.0))
    for frame in range(4):
        output = locator.observe(
            frame_id=f"camera:oversized:{frame}", timestamp_monotonic_ns=time.monotonic_ns(),
            detections=[_detection([1043, 21, 219, 734])], width=1280, height=800,
        )[0]
        assert output.observation is None
        assert output.reason == "DETECTION_BOX_TOO_TALL_FOR_GEOLOCATION"
    assert locator.tracker.estimates() == []


def test_reacquisition_updates_one_persistent_track_and_same_frame_people_stay_distinct() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    box = _box_for_target(locator, 2.0, 1.0)
    first = locator.observe(frame_id="camera:1", timestamp_monotonic_ns=time.monotonic_ns(), detections=[_detection(box)], width=1280, height=800)[0]
    # A temporary gap adds no observation and therefore cannot delete the track.
    later = locator.observe(frame_id="camera:2", timestamp_monotonic_ns=time.monotonic_ns(), detections=[_detection(box)], width=1280, height=800)[0]
    assert first.observation is not None and later.observation is not None
    assert later.observation.target_track_id == first.observation.target_track_id
    assert len(locator.tracker.estimates()) == 1

    distinct = _locator()
    distinct.update_vehicle_pose(_vehicle())
    outcomes = distinct.observe(
        frame_id="camera:one-frame", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(distinct, 0.0, 0.0)), _detection(_box_for_target(distinct, 1.0, 0.0))],
        width=1280, height=800,
    )
    ids = {item.observation.target_track_id for item in outcomes if item.observation is not None}
    assert len(ids) == 2
    assert len(distinct.tracker.estimates()) == 2
    repeated = distinct.observe(
        frame_id="camera:next-frame", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(distinct, 0.0, 0.0)), _detection(_box_for_target(distinct, 1.0, 0.0))],
        width=1280, height=800,
    )
    repeated_ids = {item.observation.target_track_id for item in repeated if item.observation is not None}
    assert repeated_ids == ids
    assert len(distinct.tracker.estimates()) == 2


def test_two_repeated_people_keep_two_tracks_despite_location_jitter() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    first_ids: set[str] | None = None
    base = time.monotonic_ns()
    # The two persons are observed repeatedly from a moving/jittering camera
    # solution.  Their projected positions vary by about 30 cm, not by a
    # hard-coded survivor identity.
    for frame, jitter in enumerate((0.0, 0.25, -0.18), start=1):
        outcomes = locator.observe(
            frame_id=f"camera:{frame}", timestamp_monotonic_ns=base + frame * 100_000_000,
            detections=[
                _detection(_box_for_target(locator, 2.0 + jitter, 1.0 - jitter)),
                _detection(_box_for_target(locator, 7.0 + jitter, 1.0 + jitter)),
            ], width=1280, height=800,
        )
        ids = {item.observation.target_track_id for item in outcomes if item.observation is not None}
        assert len(ids) == 2
        if first_ids is None:
            first_ids = ids
        else:
            assert ids == first_ids
    estimates = locator.tracker.estimates()
    assert len(estimates) == 2
    assert all(item.observation_count == 3 for item in estimates)


def test_nearly_coincident_same_frame_boxes_do_not_create_a_second_identity() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    base = time.monotonic_ns()
    stable_id = None
    for frame in range(1, 4):
        first = _box_for_target(locator, 2.0, 1.0)
        duplicate = _box_for_target(locator, 2.2, 1.1)
        outcomes = locator.observe(
            frame_id=f"camera:duplicate:{frame}",
            timestamp_monotonic_ns=base + frame * 100_000_000,
            detections=[_detection(first), _detection(duplicate)],
            width=1280, height=800,
        )
        ids = {item.observation.target_track_id for item in outcomes if item.observation is not None}
        assert len(ids) == 1
        stable_id = stable_id or next(iter(ids))
        assert ids == {stable_id}
    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    # One independent temporal observation per frame; the duplicate box does
    # not inflate confirmation evidence.
    assert estimates[0].observation_count == 3


def test_duplicate_delivery_of_one_observation_is_idempotent() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    result = locator.observe(
        frame_id="camera:one", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(locator, 2.0, 1.0))], width=1280, height=800,
    )[0]
    assert result.observation is not None
    original_id = result.observation.target_track_id
    replay = locator.tracker.process(result.observation)
    assert replay is not None
    assert replay.target_id == original_id
    assert len(locator.tracker.estimates()) == 1
    assert locator.tracker.estimates()[0].observation_count == 1


def test_new_mission_has_no_stale_live_tracks() -> None:
    first = _locator()
    first.update_vehicle_pose(_vehicle())
    observed = first.observe(
        frame_id="camera:one", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(first, 2.0, 1.0))], width=1280, height=800,
    )[0]
    assert observed.observation is not None

    next_mission = LiveSurvivorGeolocator("mission-next", ORIGIN, first.camera)
    next_mission.update_vehicle_pose(_vehicle())
    next_observed = next_mission.observe(
        frame_id="camera:one", timestamp_monotonic_ns=time.monotonic_ns(),
        detections=[_detection(_box_for_target(next_mission, 2.0, 1.0))], width=1280, height=800,
    )[0]
    assert next_observed.observation is not None
    assert next_observed.observation.target_track_id != observed.observation.target_track_id
    assert len(next_mission.tracker.estimates()) == 1


def test_two_survivors_over_multiple_passes_retain_exactly_two_canonical_identities() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    base_ts = time.monotonic_ns()

    # Pass 1: Drone sweeps past Survivor 1 (2.0, 1.0) and Survivor 2 (8.0, 4.0)
    for frame in range(1, 4):
        locator.observe(
            frame_id=f"pass1:{frame}",
            timestamp_monotonic_ns=base_ts + frame * 100_000_000,
            detections=[
                _detection(_box_for_target(locator, 2.0, 1.0)),
                _detection(_box_for_target(locator, 8.0, 4.0)),
            ],
            width=1280, height=800,
        )

    estimates = locator.tracker.estimates()
    assert len(estimates) == 2
    pass1_ids = {e.target_id for e in estimates}

    # Pass 2: Drone returns after 90 seconds (opposite sweep direction / slight jitter)
    pass2_base = base_ts + 90_000_000_000
    for frame in range(1, 4):
        locator.observe(
            frame_id=f"pass2:{frame}",
            timestamp_monotonic_ns=pass2_base + frame * 100_000_000,
            detections=[
                _detection(_box_for_target(locator, 2.2, 1.1)),
                _detection(_box_for_target(locator, 7.8, 3.9)),
            ],
            width=1280, height=800,
        )

    # Exactly 2 canonical survivor identities must remain, no duplicates created
    final_estimates = locator.tracker.estimates()
    assert len(final_estimates) == 2
    assert {e.target_id for e in final_estimates} == pass1_ids


def test_distinct_survivors_one_metre_apart_remain_separate_in_live_locator() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    base_ts = time.monotonic_ns()

    # Two distinct survivors 1.0m apart: (3.0, 2.0) and (4.0, 2.0)
    box_a = _box_for_target(locator, 3.0, 2.0)
    box_b = _box_for_target(locator, 4.0, 2.0)

    for frame in range(1, 5):
        outcomes = locator.observe(
            frame_id=f"frame:{frame}",
            timestamp_monotonic_ns=base_ts + frame * 100_000_000,
            detections=[_detection(box_a), _detection(box_b)],
            width=1280, height=800,
        )
        assert len(outcomes) == 2
        id_a = outcomes[0].observation.target_track_id
        id_b = outcomes[1].observation.target_track_id
        assert id_a != id_b

    estimates = locator.tracker.estimates()
    assert len(estimates) == 2
    assert abs(estimates[0].east_m - estimates[1].east_m) == pytest.approx(1.0, abs=0.15)


def test_duplicate_merge_pops_superseded_event() -> None:
    locator = _locator()
    locator.update_vehicle_pose(_vehicle())
    base_ts = time.monotonic_ns()

    # Establish canonical track
    for frame in range(1, 4):
        locator.observe(
            frame_id=f"frame:{frame}",
            timestamp_monotonic_ns=base_ts + frame * 100_000_000,
            detections=[_detection(_box_for_target(locator, 2.0, 1.0))],
            width=1280, height=800,
        )
    canonical_id = locator.tracker.estimates()[0].target_id

    # Create a sub-person duplicate track with excluded_track_ids
    obs = locator.tracker.tracks[canonical_id].observations[0]
    dup_obs = obs.model_copy(update={
        "observation_id": "dup-obs-1",
        "frame_id": "frame:dup:1",
        "target_east_m": 2.3,
        "target_north_m": 1.2,
    })
    locator.tracker.process(dup_obs, excluded_track_ids={canonical_id})

    # The merge should have happened and recorded a pop_merged_events entry
    merged = locator.pop_merged_events()
    assert len(merged) == 1
    assert merged[0][1] == canonical_id
    assert locator.pop_merged_events() == []


def test_repeated_detections_across_many_frames_produce_single_confirmed_identity() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # 15 frames of the same person at (2.0, 1.0)
    for frame in range(1, 16):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        outcomes = locator.observe(
            frame_id=f"frame:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.0, 1.0))],
            width=1280, height=800,
        )
        assert len(outcomes) == 1

    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    assert estimates[0].state.value == "CONFIRMED"
    assert estimates[0].observation_count == 15


def test_projected_location_varies_0_7_to_1_8_m_links_to_same_person() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Center at (2.0, 1.0), jitters from 0.7m up to 1.7m
    jitters = [
        (0.0, 0.0),       # dist 0.0m
        (0.5, 0.5),       # dist 0.71m
        (0.8, -0.6),      # dist 1.00m
        (-0.9, 0.8),      # dist 1.20m
        (1.1, 1.0),       # dist 1.49m
        (-1.2, -1.2),     # dist 1.70m
    ]
    canonical_id = None
    for frame, (je, jn) in enumerate(jitters, start=1):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        outcomes = locator.observe(
            frame_id=f"jitter_frame:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.0 + je, 1.0 + jn))],
            width=1280, height=800,
        )
        assert len(outcomes) == 1
        obs = outcomes[0].observation
        assert obs is not None
        if canonical_id is None:
            canonical_id = obs.target_track_id
        else:
            assert obs.target_track_id == canonical_id

    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    assert estimates[0].target_id == canonical_id
    assert estimates[0].state.value == "CONFIRMED"
    assert estimates[0].observation_count == len(jitters)


def test_temporary_disappearance_and_reacquisition_preserves_canonical_identity() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Pass 1: Observed for 3 frames
    for frame in range(1, 4):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"pass1:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.0, 1.0))],
            width=1280, height=800,
        )
    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    original_id = estimates[0].target_id
    assert estimates[0].state.value == "CONFIRMED"

    # Temporary disappearance: 120 seconds gap
    gap_ts = base_ts + 120_000_000_000
    for frame in range(1, 4):
        ts = gap_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"pass2:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.8, 1.6))],  # ~1.0m shift
            width=1280, height=800,
        )

    final_estimates = locator.tracker.estimates()
    assert len(final_estimates) == 1
    assert final_estimates[0].target_id == original_id
    assert final_estimates[0].observation_count == 6


def test_low_confidence_observations_do_not_prematurely_confirm() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Sub-threshold observation (conf=0.18 < 0.25 minimum_detection_confidence)
    ts = base_ts + 100_000_000
    locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
    box = _box_for_target(locator, 2.0, 1.0)
    subthreshold_det = {"class_id": 0, "class": "item", "conf": 0.18, "bbox": box}
    outcomes = locator.observe(
        frame_id="sub_thresh:1",
        timestamp_monotonic_ns=ts,
        detections=[subthreshold_det],
        width=1280, height=800,
    )
    assert len(outcomes) == 1
    # Below minimum_detection_confidence, produces no active track
    assert locator.tracker.estimates() == []

    # Single valid observation does not immediately confirm; must remain CANDIDATE
    ts2 = base_ts + 200_000_000
    locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts2)
    candidate_det = {"class_id": 0, "class": "item", "conf": 0.50, "bbox": box}
    outcomes2 = locator.observe(
        frame_id="cand:1",
        timestamp_monotonic_ns=ts2,
        detections=[candidate_det],
        width=1280, height=800,
    )
    assert len(outcomes2) == 1
    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    assert estimates[0].state.value == "CANDIDATE"


def test_reconcile_duplicate_tracks_leaves_no_duplicate_confirmed() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Track 1 confirmed
    for frame in range(1, 4):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"t1:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.0, 1.0))],
            width=1280, height=800,
        )
    t1_id = locator.tracker.estimates()[0].target_id

    # Create a near-coincident duplicate box with a separate delivery ID.
    t1_obs = locator.tracker.tracks[t1_id].observations[0]
    t2_obs = t1_obs.model_copy(update={
        "observation_id": "t2-obs-1",
        "frame_id": "t2:1",
        "target_east_m": 2.3,
        "target_north_m": 1.2,
    })
    locator.tracker.process(t2_obs, excluded_track_ids={t1_id})

    # Merge must have triggered
    events = locator.pop_merged_events()
    assert len(events) >= 1
    # Only 1 canonical estimate remains
    active_estimates = locator.tracker.estimates()
    assert len(active_estimates) == 1
    assert active_estimates[0].target_id == t1_id


def test_unique_survivor_count_and_canonical_ids_consistent_after_repeated_updates() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Two distinct survivors (2.0, 1.0) and (7.0, 3.0)
    box_1 = _box_for_target(locator, 2.0, 1.0)
    box_2 = _box_for_target(locator, 7.0, 3.0)

    # Observe across 10 frames
    for frame in range(1, 11):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"f:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(box_1), _detection(box_2)],
            width=1280, height=800,
        )

    estimates_mid = locator.tracker.estimates()
    assert len(estimates_mid) == 2
    canonical_ids = {e.target_id for e in estimates_mid}

    # Repeat with duplicate boxes and slight jitter
    box_1_dup = _box_for_target(locator, 2.1, 1.05)
    box_2_dup = _box_for_target(locator, 7.15, 2.95)

    for frame in range(11, 21):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"f:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(box_1), _detection(box_1_dup), _detection(box_2), _detection(box_2_dup)],
            width=1280, height=800,
        )

    estimates_final = locator.tracker.estimates()
    assert len(estimates_final) == 2
    assert {e.target_id for e in estimates_final} == canonical_ids
    assert all(e.state.value == "CONFIRMED" for e in estimates_final)


def test_ground_truth_isolation_and_one_to_one_evaluation_matching() -> None:
    locator = _locator()
    base_ts = time.monotonic_ns()

    # Autonomous observation of Survivor A at (2.0, 1.0)
    for frame in range(1, 4):
        ts = base_ts + frame * 100_000_000
        locator.update_vehicle_pose(_vehicle(), monotonic_time_ns=ts)
        locator.observe(
            frame_id=f"frame:{frame}",
            timestamp_monotonic_ns=ts,
            detections=[_detection(_box_for_target(locator, 2.0, 1.0))],
            width=1280, height=800,
        )

    # Autonomous tracker has exactly 1 survivor
    estimates = locator.tracker.estimates()
    assert len(estimates) == 1
    autonomous_id = estimates[0].target_id

    # Simulated scenario ground truth with 2 people:
    # Person 1 near (2.05, 1.05) and Person 2 near (2.5, 1.2)
    gt_survivors = [
        {"id": "gt-1", "east": 2.05, "north": 1.05},
        {"id": "gt-2", "east": 2.50, "north": 1.20},
    ]

    # Ground truth entries must NOT inflate the autonomous tracker
    assert len(locator.tracker.estimates()) == 1

    # 1-to-1 evaluation matching:
    # A single autonomous survivor cannot be assigned to both ground-truth survivors
    matched_autonomous = set()
    matches = {}
    for gt in gt_survivors:
        best_target = None
        best_dist = float("inf")
        for est in estimates:
            if est.target_id in matched_autonomous:
                continue
            dist = math.hypot(gt["east"] - est.east_m, gt["north"] - est.north_m)
            if dist < best_dist:
                best_dist = dist
                best_target = est
        if best_target is not None and best_dist < 2.0:
            matched_autonomous.add(best_target.target_id)
            matches[gt["id"]] = best_target.target_id
        else:
            matches[gt["id"]] = None

    # gt-1 matched with autonomous_id, but gt-2 cannot reuse it
    assert matches["gt-1"] == autonomous_id
    assert matches["gt-2"] is None
    # Autonomous tracker count remains strictly independent
    assert len(locator.tracker.estimates()) == 1



