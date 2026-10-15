from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import RejectedObservation, TargetObservation, TargetPixelStrategy, TargetTrackState
from rescue_core.geolocation.projection import SyntheticTargetProjector
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.vision.models import Detection, DetectionResult

from .test_geolocation_helpers import coordinate_frame, nadir_extrinsics, synced_history, synthetic_calibration


def _result(timestamp_ns: int, pixel: tuple[float, float], strategy: TargetPixelStrategy) -> tuple[DetectionResult, Detection]:
    x, y = pixel
    if strategy is TargetPixelStrategy.BBOX_CENTER:
        box = (x - 5, y - 10, x + 5, y + 10)
    else:
        box = (x - 5, y - 20, x + 5, y)
    detection = Detection(
        class_id=0, class_name="person", confidence=0.9,
        x1=box[0], y1=box[1], x2=box[2], y2=box[3],
        frame_width=1280, frame_height=800,
    )
    result = DetectionResult(
        frame_id=f"frame:{timestamp_ns}", source_id="synthetic_projection",
        capture_timestamp_monotonic_ns=timestamp_ns,
        capture_timestamp_wall_ns=timestamp_ns + 1_700_000_000_000_000_000,
        inference_started_ns=timestamp_ns + 1_000_000,
        inference_finished_ns=timestamp_ns + 2_000_000,
        model_id="SYNTHETIC_TEST_ONLY", backend="synthetic_projector",
        original_channel_format="Y8", model_input_format="NONE",
        detections=[detection], preprocess_ms=0, inference_ms=0, postprocess_ms=0, end_to_end_ms=2,
    )
    return result, detection


def _geolocator(strategy: TargetPixelStrategy = TargetPixelStrategy.BBOX_CENTER):
    calibration, extrinsics = synthetic_calibration(), nadir_extrinsics()
    history, clock, offset = synced_history()
    frame = coordinate_frame()
    uncertainty = GeolocationUncertaintyEstimator(3, 5, 2, 1, 2, 1)
    geolocator = TargetGeolocator(
        calibration=calibration, extrinsics=extrinsics, pose_history=history,
        clock_sync=clock, ground_model=FlatGroundModel(),
        wgs84_converter=frame.enu_to_wgs84, uncertainty=uncertainty,
        pixel_strategy=strategy, require_sync=True,
    )
    projector = SyntheticTargetProjector(CameraModel.from_calibration(calibration), extrinsics)
    return geolocator, projector, history, clock, offset, frame


@pytest.mark.parametrize("strategy", [TargetPixelStrategy.BBOX_CENTER, TargetPixelStrategy.BBOX_BOTTOM_CENTER])
def test_zero_noise_forward_inverse_geolocation_is_numerically_exact(strategy: TargetPixelStrategy) -> None:
    geolocator, projector, history, clock, _, _ = _geolocator(strategy)
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    known = np.array([3.0, 4.0, 0.0])
    pixel = projector.project(known, pose)
    assert pixel is not None
    result, detection = _result(capture, pixel, strategy)
    observation = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(observation, TargetObservation)
    assert [observation.target_east_m, observation.target_north_m, observation.target_up_m] == pytest.approx(known, abs=1e-8)
    assert observation.position_uncertainty_m >= 2  # Accurate geometry does not erase GNSS uncertainty.


def test_quality_gates_reject_unsynced_mismatch_invalid_gps_and_sky_ray() -> None:
    geolocator, projector, history, clock, _, _ = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([0, 0, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    mismatch = geolocator.geolocate(result, detection, "drone_01", active_width=640, active_height=480, active_sensor_mode="OTHER")
    assert isinstance(mismatch, RejectedObservation) and "CALIBRATION_MISMATCH" in mismatch.reason
    geolocator.clock_sync = None
    unsynced = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(unsynced, RejectedObservation) and unsynced.reason == "CLOCK_UNSYNCED"
    geolocator.clock_sync = clock
    geolocator.mission_region_validator = lambda _east, _north: False
    outside = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(outside, RejectedObservation) and outside.reason == "TARGET_OUTSIDE_MISSION_REGION"
    geolocator.mission_region_validator = None
    for sample in history.samples:
        sample.gps_fix = 1
    invalid_gps = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(invalid_gps, RejectedObservation) and invalid_gps.reason == "GPS_INVALID"


def test_timestamp_offset_causes_error_for_moving_vehicle() -> None:
    geolocator, projector, history, clock, _, _ = _geolocator()
    true_capture = 2_100_000_000
    true_pose = history.lookup(true_capture, clock).sample
    target = np.array([0.0, 5.0, 0.0])
    pixel = projector.project(target, true_pose)
    correct_result, detection = _result(true_capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    correct = geolocator.geolocate(correct_result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    stale_result, stale_detection = _result(true_capture - 100_000_000, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    stale = geolocator.geolocate(stale_result, stale_detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(correct, TargetObservation) and isinstance(stale, TargetObservation)
    correct_error = np.linalg.norm(np.array([correct.target_east_m, correct.target_north_m]) - target[:2])
    stale_error = np.linalg.norm(np.array([stale.target_east_m, stale.target_north_m]) - target[:2])
    assert correct_error < 1e-8
    assert stale_error == pytest.approx(0.4, abs=1e-6)


def _observation(base: TargetObservation, east: float, north: float, seconds: float, uncertainty: float = 3) -> TargetObservation:
    return base.model_copy(update={
        "observation_id": f"obs-{east}-{north}-{seconds}", "target_east_m": east,
        "target_north_m": north, "target_latitude": base.target_latitude,
        "target_longitude": base.target_longitude, "position_uncertainty_m": uncertainty,
        "uncertainty_components": {"common_bias_floor_m": 2.0},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
    })


def test_robust_fusion_rejects_large_outlier_and_preserves_common_bias_floor() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    observations = [
        _observation(base, 10.0, 20.0, 0), _observation(base, 10.2, 19.9, 1),
        _observation(base, 9.9, 20.1, 2), _observation(base, 25.0, 35.0, 3),
    ]
    estimate = TargetFusionEngine().fuse("mission", "target", observations, frame.enu_to_wgs84, TargetTrackState.CONFIRMED)
    assert [estimate.east_m, estimate.north_m] == pytest.approx([10.0333, 20.0], abs=0.15)
    assert estimate.outlier_count == 1
    assert estimate.uncertainty_m >= 2.0
    biased = [item.model_copy(update={"target_east_m": item.target_east_m + 2}) for item in observations[:3]]
    biased_estimate = TargetFusionEngine().fuse("mission", "biased", biased, frame.enu_to_wgs84, TargetTrackState.CONFIRMED)
    assert biased_estimate.east_m == pytest.approx(12.0333, abs=0.15)  # Fusion cannot remove common GNSS bias.


def test_confirmation_is_not_consecutive_and_suppresses_late_duplicate() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="mission", fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
        minimum_observations=3, minimum_time_span_s=1, time_window_s=8, association_radius_m=4,
    )
    first = tracker.process(_observation(base, 10, 20, 0))
    second = tracker.process(_observation(base, 10.1, 20, 2))
    third = tracker.process(_observation(base, 9.9, 20.1, 5))
    assert first and first.state is TargetTrackState.CANDIDATE
    assert second and second.state is TargetTrackState.CANDIDATE
    assert third and third.state is TargetTrackState.CONFIRMED
    late = tracker.process(_observation(base, 10.2, 20, 20))
    assert late and late.target_id == third.target_id and len(tracker.tracks) == 1
    separate = tracker.process(_observation(base, 40, 50, 21))
    assert separate and separate.state is TargetTrackState.CANDIDATE and len(tracker.tracks) == 2


def test_three_valid_low_confidence_observations_use_configured_confirmation_count() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="low-confidence", fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
        minimum_observations=3, minimum_time_span_s=.15, association_radius_m=4,
        minimum_detection_confidence=.25,
    )

    estimates = []
    for index, confidence in enumerate((.296, .292, .261)):
        observation = _observation(base, 10 + index * .05, 20 + index * .04, index * .3)
        observation.detection_confidence = confidence
        observation.bbox_original_px = (300 + index * 15, 250 + index * 20, 500 + index * 15, 700)
        estimates.append(tracker.process(observation))

    assert estimates[0] and estimates[0].state is TargetTrackState.CANDIDATE
    assert estimates[1] and estimates[1].state is TargetTrackState.CANDIDATE
    assert estimates[2] and estimates[2].state is TargetTrackState.CONFIRMED
    assert len(tracker.tracks) == 1


def test_fast_image_motion_with_consistent_world_location_keeps_one_identity() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="fast-pixels", fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
        minimum_observations=3, minimum_time_span_s=.15, association_radius_m=6,
        short_term_pixel_radius_px=35,
    )
    boxes = [(10, 20, 30, 60), (20, 105, 40, 145), (35, 190, 55, 230)]
    estimates = []
    for index, box in enumerate(boxes):
        observation = _observation(base, 10 + index * .3, 20 + index * .1, index * .3)
        observation.bbox_original_px = box
        estimates.append(tracker.process(observation))

    assert len(tracker.tracks) == 1
    assert estimates[-1] and estimates[-1].state is TargetTrackState.CONFIRMED


def test_confirmed_track_reacquires_within_reported_uncertainty_after_live_scale_shift() -> None:
    """A cross-view shift may exceed 6 m without exceeding the 2.5-sigma gate."""
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="live-reacquisition", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, time_window_s=60, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )
    observations = []
    for index, east in enumerate((10.0, 10.2, 9.9)):
        observation = _observation(base, east, 20.0, index * .3)
        observation.position_uncertainty_m = 2.4
        observations.append(tracker.process(observation))
    stable_id = observations[-1].target_id  # type: ignore[union-attr]

    reacquired = _observation(base, 16.8, 20.0, 16.0)
    reacquired.position_uncertainty_m = 2.6
    estimate = tracker.process(reacquired)

    assert estimate is not None
    assert estimate.target_id == stable_id
    assert len(tracker.tracks) == 1


def test_high_iou_same_frame_model_boxes_are_one_observation() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="same-frame-iou", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
    )
    first = _observation(base, 10.0, 20.0, 0)
    first.frame_id = "same-frame"
    first.bbox_original_px = (1037, 0, 1259, 208)
    first_estimate = tracker.process(first)
    assert first_estimate is not None

    duplicate = _observation(base, 11.5, 21.0, 0)
    duplicate.frame_id = "same-frame"
    duplicate.bbox_original_px = (1047, 0, 1260, 208)
    duplicate_estimate = tracker.process(duplicate, excluded_track_ids={first_estimate.target_id})

    assert duplicate_estimate is not None
    assert duplicate_estimate.target_id == first_estimate.target_id
    assert len(tracker.tracks) == 1
    assert tracker.estimates()[0].observation_count == 1


def test_contained_same_frame_model_box_is_one_observation() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="same-frame-contained", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, association_radius_m=8,
    )
    primary = _observation(base, 40.0, -40.1, 0)
    primary.frame_id = "camera:6948"
    primary.bbox_original_px = (497, 614, 670, 725)
    primary_estimate = tracker.process(primary)
    assert primary_estimate is not None

    partial = _observation(base, 40.0, -40.9, 0)
    partial.frame_id = "camera:6948"
    partial.bbox_original_px = (499, 612, 564, 722)
    partial_estimate = tracker.process(partial, excluded_track_ids={primary_estimate.target_id})

    assert partial_estimate is not None
    assert partial_estimate.target_id == primary_estimate.target_id
    assert len(tracker.tracks) == 1
    assert tracker.estimates()[0].observation_count == 1


def test_adjacent_frame_continuity_uses_latest_raw_observation() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)  # type: ignore[arg-type]
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="adjacent-frame-continuity", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )
    # Establish a robust fused centre around x=31, then reproduce the live
    # viewpoint shift whose latest raw point is near x=40.
    for index, east in enumerate((31.0, 31.2, 30.8)):
        item = _observation(base, east, -40.0, index * .3)
        item.position_uncertainty_m = 2.0
        estimate = tracker.process(item)
    assert estimate is not None and estimate.state is TargetTrackState.CONFIRMED
    transition = _observation(base, 37.2, -40.1, 1.8)
    transition.position_uncertainty_m = 2.2
    transition_estimate = tracker.process(transition)
    assert transition_estimate is not None
    shifted = _observation(base, 40.1, -40.1, 2.0)
    shifted.position_uncertainty_m = 2.2
    shifted_estimate = tracker.process(shifted)
    assert shifted_estimate is not None
    # Fusion still resists the shift, but the next adjacent frame must follow
    # the latest raw observation instead of spawning a second identity.
    adjacent = _observation(base, 40.3, -40.1, 2.15)
    adjacent.position_uncertainty_m = 2.2
    adjacent_estimate = tracker.process(adjacent)

    assert adjacent_estimate is not None
    assert adjacent_estimate.target_id == shifted_estimate.target_id
    assert len(tracker.tracks) == 1


def test_two_physical_survivors_produce_exactly_two_canonical_identities() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="two-survivors-two-identities", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )

    # Person 1 around (5.0, 5.0), Person 2 around (18.0, 18.0)
    for frame_idx in range(5):
        t = frame_idx * 0.2
        p1 = _observation(base, 5.0 + 0.1 * frame_idx, 5.0 - 0.1 * frame_idx, t)
        p1.frame_id = f"cam:{frame_idx}"
        p1.bbox_original_px = (200 + frame_idx, 300, 240 + frame_idx, 380)
        e1 = tracker.process(p1)

        p2 = _observation(base, 18.0 - 0.1 * frame_idx, 18.0 + 0.1 * frame_idx, t)
        p2.frame_id = f"cam:{frame_idx}"
        p2.bbox_original_px = (800 + frame_idx, 400, 840 + frame_idx, 480)
        e2 = tracker.process(p2, excluded_track_ids={e1.target_id} if e1 else set())

    estimates = tracker.estimates()
    assert len(estimates) == 2
    assert all(est.state is TargetTrackState.CONFIRMED for est in estimates)
    assert len(set(est.target_id for est in estimates)) == 2


def test_repeated_observations_of_one_person_over_many_frames_retain_one_identity() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="repeated-observations-single-id", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
    )

    stable_id: str | None = None
    for i in range(25):
        obs = _observation(base, 10.0 + (i % 3) * 0.1, 12.0 - (i % 2) * 0.1, i * 0.1)
        obs.frame_id = f"camera:{i}"
        obs.bbox_original_px = (500 + i, 400, 540 + i, 480)
        est = tracker.process(obs)
        assert est is not None
        stable_id = stable_id or est.target_id
        assert est.target_id == stable_id

    estimates = tracker.estimates()
    assert len(estimates) == 1
    assert estimates[0].target_id == stable_id
    assert estimates[0].observation_count == 25


def test_rediscovery_after_temporary_gap_does_not_create_second_active_identity() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="rediscovery-gap", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )

    # Initial pass confirms target
    for i in range(4):
        obs = _observation(base, 14.0, 15.0, i * 0.2)
        obs.frame_id = f"pass1:{i}"
        est = tracker.process(obs)
    assert est is not None and est.state is TargetTrackState.CONFIRMED
    canonical_id = est.target_id

    # Rediscovery after 120s detection gap (e.g. adjacent lawnmower pass / return pass)
    for i in range(3):
        rediscovered = _observation(base, 14.3, 14.8, 120.0 + i * 0.2)
        rediscovered.frame_id = f"pass2:{i}"
        re_est = tracker.process(rediscovered)
        assert re_est is not None
        assert re_est.target_id == canonical_id

    assert len(tracker.estimates()) == 1
    assert tracker.estimates()[0].target_id == canonical_id


def test_two_distinct_people_one_metre_apart_remain_separate() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="one-metre-separation", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )

    # Person A at (20.0, 20.0), Person B at (21.0, 20.0) -> exactly 1.0m apart
    # In each frame, both appear with non-overlapping bounding boxes
    for frame_idx in range(4):
        t = frame_idx * 0.2
        frame_id = f"frame:{frame_idx}"

        obs_a = _observation(base, 20.0, 20.0, t)
        obs_a.frame_id = frame_id
        obs_a.bbox_original_px = (300, 400, 340, 480)
        est_a = tracker.process(obs_a)
        assert est_a is not None

        obs_b = _observation(base, 21.0, 20.0, t)
        obs_b.frame_id = frame_id
        obs_b.bbox_original_px = (420, 400, 460, 480)  # non-overlapping boxes
        est_b = tracker.process(obs_b, excluded_track_ids={est_a.target_id})
        assert est_b is not None

        assert est_a.target_id != est_b.target_id

    estimates = tracker.estimates()
    assert len(estimates) == 2
    assert abs(estimates[0].east_m - estimates[1].east_m) == pytest.approx(1.0, abs=0.1)


def test_duplicate_track_merge_preserves_valid_evidence_and_resolves_old_id() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="merge-preserves-evidence", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )

    # Track 1 confirmed from pass 1
    for i in range(3):
        obs = _observation(base, 25.0, 25.0, i * 0.2)
        obs.frame_id = f"p1:{i}"
        est1 = tracker.process(obs)
    assert est1 is not None and est1.state is TargetTrackState.CONFIRMED
    canonical_id = est1.target_id

    # Suppose a secondary candidate track was created at distance 0.4m (sub-person)
    sub_obs = _observation(base, 25.3, 25.2, 5.0)
    sub_obs.frame_id = "p2:0"
    # Process without associating to test merge:
    est_sub = tracker.process(sub_obs)
    assert est_sub is not None

    # Merge must resolve to canonical ID and preserve single active estimate
    assert tracker.canonical_id(est_sub.target_id) == canonical_id
    estimates = tracker.estimates()
    assert len(estimates) == 1
    assert estimates[0].target_id == canonical_id


def test_repeated_or_delayed_events_cannot_resurrect_superseded_duplicate() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(result, detection, "drone_01", active_width=1280, active_height=800, active_sensor_mode="SYNTHETIC_1280x800")
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="no-resurrection", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )

    # Establish canonical track
    for i in range(3):
        obs = _observation(base, 30.0, 30.0, i * 0.2)
        obs.frame_id = f"orig:{i}"
        est = tracker.process(obs)
    assert est is not None
    canonical_id = est.target_id

    # Create and merge a secondary track by temporarily excluding canonical track
    # A replayed box can carry a new event/frame ID.  The coincident image
    # timestamp and box, not geography alone, establish this alias.
    dup_obs = tracker.tracks[canonical_id].observations[0].model_copy(update={
        "observation_id": "duplicate-delivery",
        "frame_id": "dup:1",
        "target_east_m": 30.2,
        "target_north_m": 30.1,
    })
    tracker.process(dup_obs, excluded_track_ids={canonical_id})
    assert canonical_id in tracker.aliases.values()
    superseded_id = [src for src, dst in tracker.aliases.items() if dst == canonical_id][0]
    assert tracker.canonical_id(superseded_id) == canonical_id

    # Delayed event referring to superseded ID arrives
    delayed_obs = _observation(base, 30.1, 30.0, 8.0)
    delayed_obs.target_track_id = superseded_id
    delayed_est = tracker.process(delayed_obs)

    assert delayed_est is not None
    assert delayed_est.target_id == canonical_id
    assert len(tracker.estimates()) == 1
    assert tracker.tracks[superseded_id].state is TargetTrackState.REJECTED


def test_nearby_tracks_without_visual_identity_evidence_are_not_merged() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="nearby-distinct", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )
    first_id = None
    second_id = None
    for i in range(3):
        first = _observation(base, 20, 20, i * .2)
        first.frame_id = f"a:{i}"
        first.bbox_original_px = (200, 200, 250, 300)
        first_estimate = tracker.process(first, excluded_track_ids={second_id} if second_id else None)
        assert first_estimate is not None
        first_id = first_estimate.target_id

        second = _observation(base, 21, 20, i * .2 + .1)
        second.frame_id = f"b:{i}"
        second.bbox_original_px = (500, 200, 550, 300)
        second_estimate = tracker.process(second, excluded_track_ids={first_id})
        assert second_estimate is not None
        second_id = second_estimate.target_id

    assert first_id != second_id
    assert {item.target_id for item in tracker.estimates()} == {first_id, second_id}
    assert all(item.state is TargetTrackState.CONFIRMED for item in tracker.estimates())


def test_alternating_image_tracklets_reconcile_a_split_identity() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    capture = 2_050_000_000
    pose = history.lookup(capture, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(capture, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="visual-handoff", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
        confirmed_reacquisition_radius_m=10,
    )
    for i in range(3):
        obs = _observation(base, 20, 20, i * .2)
        obs.frame_id = f"primary:{i}"
        obs.bbox_original_px = (200 + i * 10, 200, 280 + i * 10, 320)
        primary = tracker.process(obs)
    assert primary is not None and primary.state is TargetTrackState.CONFIRMED

    split = _observation(base, 22, 20, .6)
    split.frame_id = "split:0"
    split.bbox_original_px = (230, 200, 310, 320)
    tracker.process(split, excluded_track_ids={primary.target_id})
    assert len(tracker.estimates()) == 2

    continuation = _observation(base, 20, 20, .8)
    continuation.frame_id = "primary:3"
    continuation.bbox_original_px = (240, 200, 320, 320)
    tracker.process(continuation, excluded_track_ids={split.target_track_id})
    assert len(tracker.estimates()) == 1
    assert tracker.estimates()[0].target_id == primary.target_id
    assert tracker.estimates()[0].observation_count == 5


def test_partial_same_frame_overlap_does_not_suppress_distinct_people() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    pose = history.lookup(2_050_000_000, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(2_050_000_000, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="overlapping-distinct", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, association_radius_m=8,
    )
    first = _observation(base, 20, 20, 0)
    first.frame_id = "camera:shared"
    first.bbox_original_px = (200, 200, 300, 320)
    first_estimate = tracker.process(first)
    assert first_estimate is not None
    second = _observation(base, 21, 20, 0)
    second.frame_id = first.frame_id
    second.bbox_original_px = (265, 200, 365, 320)
    second_estimate = tracker.process(second, excluded_track_ids={first_estimate.target_id})
    assert second_estimate is not None
    assert second_estimate.target_id != first_estimate.target_id


def test_reconciliation_keeps_earliest_confirmed_id_even_if_new_track_is_larger() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    pose = history.lookup(2_050_000_000, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(2_050_000_000, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="durable-id", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
    )
    for i in range(3):
        first = _observation(base, 20, 20, i * .2)
        first.frame_id = f"first:{i}"
        first.bbox_original_px = (200, 200, 250, 300)
        first_estimate = tracker.process(first)
    assert first_estimate is not None and first_estimate.state is TargetTrackState.CONFIRMED

    for i in range(5):
        second = _observation(base, 22, 20, 1 + i * .2)
        second.frame_id = f"second:{i}"
        second.bbox_original_px = (500, 200, 550, 300)
        second_estimate = tracker.process(second, excluded_track_ids={first_estimate.target_id})
    assert second_estimate is not None and second_estimate.state is TargetTrackState.CONFIRMED
    assert second_estimate.observation_count > first_estimate.observation_count

    merged = tracker._merge_tracks(
        tracker.tracks[first_estimate.target_id], tracker.tracks[second_estimate.target_id]
    )
    assert merged.target_id == first_estimate.target_id
    assert tracker.canonical_id(second_estimate.target_id) == first_estimate.target_id
    assert len(tracker.estimates()) == 1


def test_alternating_boxes_at_one_ground_point_do_not_claim_two_people() -> None:
    geolocator, projector, history, clock, _, frame = _geolocator()
    pose = history.lookup(2_050_000_000, clock).sample
    pixel = projector.project(np.array([1, 2, 0]), pose)
    result, detection = _result(2_050_000_000, pixel, TargetPixelStrategy.BBOX_CENTER)
    base = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    assert isinstance(base, TargetObservation)
    tracker = SpatiotemporalTargetTracker(
        mission_id="split-body-boxes", fusion=TargetFusionEngine(),
        wgs84_converter=frame.enu_to_wgs84, minimum_observations=3,
        minimum_time_span_s=.15, association_radius_m=8,
    )
    first_id = second_id = None
    for i in range(3):
        first = _observation(base, 20, 20, i * .2)
        first.frame_id = f"camera:{i}"
        first.bbox_original_px = (200, 200, 240, 240)
        a = tracker.process(first, excluded_track_ids={second_id} if second_id else None)
        assert a is not None
        first_id = a.target_id

        second = _observation(base, 20.2, 20.1, i * .2)
        second.frame_id = first.frame_id
        second.bbox_original_px = (255, 200, 295, 240)
        b = tracker.process(second, excluded_track_ids={first_id})
        assert b is not None
        second_id = b.target_id

    active = tracker.estimates()
    if len(active) > 1:
        tracks = [tracker.tracks[item.target_id] for item in active]
        assert tracker._visual_handoff_count(*tracks) >= 2, [
            [(obs.frame_id, obs.created_at.isoformat(), obs.bbox_original_px) for obs in track.observations]
            for track in tracks
        ]
        assert not tracker._have_cooccurring_frames(*tracks)
    assert len(active) == 1
    assert active[0].observation_count == 3

