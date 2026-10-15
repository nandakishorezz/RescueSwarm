"""Deterministic Stage 5 mathematical validation; results are synthetic, never physical."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from rescue_core.geolocation.calibration import (
    CalibrationVerdict, CameraCalibration, CameraExtrinsics, DistortionModel,
    ExtrinsicsMethod, ExtrinsicsQuality,
)
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.clock_sync import ClockSyncEstimator
from rescue_core.geolocation.evaluation import geolocation_metrics
from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import TargetObservation, TargetPixelStrategy
from rescue_core.geolocation.pose import PoseHistory, PoseSample
from rescue_core.geolocation.projection import SyntheticTargetProjector
from rescue_core.geolocation.transforms import quaternion_from_euler
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator, MonteCarloUncertaintyEvaluator
from rescue_core.vision.models import Detection, DetectionResult


def synthetic_calibration() -> CameraCalibration:
    return CameraCalibration(
        calibration_id="stage5_synthetic_pinhole", camera_identifier="SYNTHETIC_TEST_ONLY",
        sensor="SYNTHETIC", resolution=(1280, 800), sensor_mode="SYNTHETIC_1280x800",
        distortion_model=DistortionModel.PINHOLE_BROWN,
        camera_matrix=[[800, 0, 640], [0, 800, 400], [0, 0, 1]],
        distortion_coefficients=[0, 0, 0, 0, 0], image_width=1280, image_height=800,
        rms_reprojection_error=0, mean_reprojection_error=0, median_reprojection_error=0,
        p95_reprojection_error=0, pattern_type="SYNTHETIC", pattern_dimensions=(9, 6),
        square_size_m=0.025, images_used=0, images_rejected=0,
        software_version="RescueSwarm synthetic gold model", verdict=CalibrationVerdict.PASS,
        validation_reasons=["SYNTHETIC TEST CALIBRATION — NOT PHYSICAL EVIDENCE"],
    ).with_hash()


def nadir_extrinsics(rotation_error_deg: float = 0) -> CameraExtrinsics:
    base = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    error = np.array([
        [1, 0, 0], [0, math.cos(math.radians(rotation_error_deg)), -math.sin(math.radians(rotation_error_deg))],
        [0, math.sin(math.radians(rotation_error_deg)), math.cos(math.radians(rotation_error_deg))],
    ])
    return CameraExtrinsics.from_rotation_matrix(
        extrinsics_id=f"stage5_synthetic_nadir_{rotation_error_deg:g}deg",
        camera_identifier="SYNTHETIC_TEST_ONLY", translation_body_m=(0, 0, 0),
        rotation_camera_to_body=base @ error, method=ExtrinsicsMethod.CALIBRATED,
        quality=ExtrinsicsQuality.CALIBRATED, rotation_uncertainty_deg=0.01,
        translation_uncertainty_m=0, notes="SYNTHETIC TEST ONLY",
    )


def pose(remote_ns: int, host_ns: int, north_m: float, east_m: float = 0, altitude_m: float = 20,
         roll_deg: float = 0, pitch_deg: float = 0, yaw_deg: float = 0) -> PoseSample:
    return PoseSample(
        vehicle_id="drone_01", autopilot_boot_time_ns=remote_ns,
        host_monotonic_time_ns=host_ns, host_wall_time_ns=1_700_000_000_000_000_000 + host_ns,
        latitude=17.45, longitude=78.38, absolute_altitude_m=500 + altitude_m,
        relative_altitude_m=altitude_m, local_position_ned_m=(north_m, east_m, -altitude_m),
        quaternion_body_to_ned=tuple(quaternion_from_euler(
            math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
        )),
        velocity_ned_m_s=(4, 0, 0), gps_fix=3, satellites=12,
        horizontal_accuracy_m=2, vertical_accuracy_m=3,
        accuracy_sources={"horizontal": "SYNTHETIC_INPUT", "vertical": "SYNTHETIC_INPUT"},
        source_message_ids=["SYNTHETIC_TEST_ONLY"],
    )


def clock_and_history(samples: list[PoseSample], offset_ns: int = 5_000_000_000) -> tuple[ClockSyncEstimator, PoseHistory]:
    clock = ClockSyncEstimator(minimum_samples=3, maximum_samples=20, max_rtt_ms=20, degraded_rtt_p95_ms=10)
    for index in range(5):
        sent = 500_000_000 + index * 10_000_000
        clock.observe_response(sent, sent + 1_000_000 + offset_ns, sent + 2_000_000)
    history = PoseHistory(max_interpolation_gap_ms=600, max_extrapolation_ms=100)
    for sample in samples:
        history.add(sample)
    return clock, history


def detection_result(frame_time_ns: int, pixel: tuple[float, float], index: int = 0) -> tuple[DetectionResult, Detection]:
    x, y = pixel
    detection = Detection(0, "person", 0.9, x - 4, y - 8, x + 4, y + 8, 1280, 800)
    return DetectionResult(
        frame_id=f"synthetic:{index:04d}", source_id="synthetic_target_projector",
        capture_timestamp_monotonic_ns=frame_time_ns,
        capture_timestamp_wall_ns=1_700_000_000_000_000_000 + frame_time_ns,
        inference_started_ns=frame_time_ns + 1_000_000, inference_finished_ns=frame_time_ns + 2_000_000,
        model_id="SYNTHETIC_TARGET_PROJECTOR", backend="TEST_ONLY", original_channel_format="NONE",
        model_input_format="NONE", detections=[detection], preprocess_ms=0, inference_ms=0,
        postprocess_ms=0, end_to_end_ms=2,
    ), detection


def _uncertainty() -> GeolocationUncertaintyEstimator:
    return GeolocationUncertaintyEstimator(3, 5, 2, 1, 2, 1)


def _inverse(
    pixel: tuple[float, float], frame_time_ns: int, samples: list[PoseSample],
    frame: LocalCoordinateFrame, calibration: CameraCalibration, assumed_extrinsics: CameraExtrinsics,
    ground_up_m: float = 0,
) -> TargetObservation:
    clock, history = clock_and_history(samples)
    result, detection = detection_result(frame_time_ns, pixel)
    geolocator = TargetGeolocator(
        calibration=calibration, extrinsics=assumed_extrinsics, pose_history=history,
        clock_sync=clock, ground_model=FlatGroundModel(ground_up_m, "SYNTHETIC_FLAT_GROUND"),
        wgs84_converter=frame.enu_to_wgs84, uncertainty=_uncertainty(),
        pixel_strategy=TargetPixelStrategy.BBOX_CENTER, require_sync=True,
    )
    observation = geolocator.geolocate(
        result, detection, "drone_01", active_width=1280, active_height=800,
        active_sensor_mode="SYNTHETIC_1280x800",
    )
    if not isinstance(observation, TargetObservation):
        raise RuntimeError(f"Synthetic inverse geolocation rejected: {observation.reason}")
    return observation


def run_synthetic_validation(seed: int = 42, monte_carlo_samples: int = 200) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    calibration, extrinsics = synthetic_calibration(), nadir_extrinsics()
    camera = CameraModel.from_calibration(calibration)
    projector = SyntheticTargetProjector(camera, extrinsics)
    frame = LocalCoordinateFrame(GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500))
    offset = 5_000_000_000
    base_host = 2_000_000_000
    true_target = np.array([4.0, 2.0, 0.0])
    poses = [pose(base_host + offset + i * 250_000_000, base_host + i * 250_000_000, north_m=-2 + i)
             for i in range(5)]
    clock, history = clock_and_history(poses, offset)
    geolocator = TargetGeolocator(
        calibration=calibration, extrinsics=extrinsics, pose_history=history, clock_sync=clock,
        ground_model=FlatGroundModel(0, "SYNTHETIC_FLAT_GROUND"), wgs84_converter=frame.enu_to_wgs84,
        uncertainty=_uncertainty(), pixel_strategy=TargetPixelStrategy.BBOX_CENTER,
    )
    tracker = SpatiotemporalTargetTracker(
        mission_id="stage5-synthetic", fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
        minimum_observations=3, minimum_time_span_s=0.1, time_window_s=8, association_radius_m=6,
    )
    observations: list[TargetObservation] = []
    estimate = None
    for index, sample in enumerate(poses):
        pixel = projector.project(true_target, sample)
        if pixel is None:
            continue
        result, detection = detection_result(sample.host_monotonic_time_ns, pixel, index)
        observation = geolocator.geolocate(
            result, detection, "drone_01", active_width=1280, active_height=800,
            active_sensor_mode="SYNTHETIC_1280x800", evidence_references=[f"synthetic_frame_{index:04d}"],
        )
        if isinstance(observation, TargetObservation):
            observation.created_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * 0.25)
            observations.append(observation)
            estimate = tracker.process(observation)
    if not estimate:
        raise RuntimeError("Synthetic target was not estimated")
    zero_error = math.hypot(estimate.east_m - true_target[0], estimate.north_m - true_target[1])

    nominal_pose = pose(base_host + offset, base_host, 0)
    nominal_pixel = projector.project(true_target, nominal_pose)
    if nominal_pixel is None:
        raise RuntimeError("Synthetic target is outside fixture field of view")

    def noisy_sampler(local_rng: np.random.Generator) -> tuple[float, float]:
        pixel = (nominal_pixel[0] + local_rng.normal(0, 1.5), nominal_pixel[1] + local_rng.normal(0, 1.5))
        north_bias, east_bias = local_rng.normal(0, 2, 2)
        noisy_samples = [pose(base_host + offset + delta, base_host + delta, north_bias, east_bias,
                              20 + local_rng.normal(0, 1), roll_deg=local_rng.normal(0, 0.5),
                              pitch_deg=local_rng.normal(0, 0.5)) for delta in (-50_000_000, 50_000_000)]
        obs = _inverse(pixel, base_host, noisy_samples, frame, calibration, extrinsics)
        return obs.target_east_m, obs.target_north_m

    monte_carlo = MonteCarloUncertaintyEvaluator(monte_carlo_samples, seed).evaluate(noisy_sampler)
    noisy_center_error = math.hypot(
        float(monte_carlo["median_east_m"]) - true_target[0],
        float(monte_carlo["median_north_m"]) - true_target[1],
    )

    timing: dict[str, float] = {}
    moving_true = pose(base_host + offset + 250_000_000, base_host + 250_000_000, 0)
    moving_pixel = projector.project(true_target, moving_true)
    moving_samples = [pose(base_host + offset + delta, base_host + delta, (delta - 250_000_000) / 1e9 * 4)
                      for delta in (0, 100_000_000, 200_000_000, 250_000_000, 300_000_000)]
    for stale_ms in (0, 100, 200):
        obs = _inverse(moving_pixel, base_host + 250_000_000 - stale_ms * 1_000_000,
                       moving_samples, frame, calibration, extrinsics)
        timing[f"{stale_ms}_ms"] = math.hypot(obs.target_east_m - true_target[0], obs.target_north_m - true_target[1])

    attitude: dict[str, float] = {}
    for error_deg in (0, 1, 2):
        bad = [pose(base_host + offset + delta, base_host + delta, 0, roll_deg=error_deg) for delta in (-50_000_000, 50_000_000)]
        obs = _inverse(nominal_pixel, base_host, bad, frame, calibration, extrinsics)
        attitude[f"roll_{error_deg}_deg"] = math.hypot(obs.target_east_m - true_target[0], obs.target_north_m - true_target[1])

    extrinsic: dict[str, float] = {}
    for error_deg in (0, 1, 2):
        obs = _inverse(nominal_pixel, base_host,
                       [pose(base_host + offset + delta, base_host + delta, 0) for delta in (-50_000_000, 50_000_000)],
                       frame, calibration, nadir_extrinsics(error_deg))
        extrinsic[f"tilt_{error_deg}_deg"] = math.hypot(obs.target_east_m - true_target[0], obs.target_north_m - true_target[1])

    biased_samples = [pose(base_host + offset + delta, base_host + delta, 0, east_m=2) for delta in (-50_000_000, 50_000_000)]
    biased = _inverse(nominal_pixel, base_host, biased_samples, frame, calibration, extrinsics)
    gnss_bias_error = math.hypot(biased.target_east_m - true_target[0], biased.target_north_m - true_target[1])

    elevated_target = np.array([4, 2, 2], dtype=float)
    elevated_pixel = projector.project(elevated_target, nominal_pose)
    ground_obs = _inverse(elevated_pixel, base_host,
                          [pose(base_host + offset + delta, base_host + delta, 0) for delta in (-50_000_000, 50_000_000)],
                          frame, calibration, extrinsics, ground_up_m=0)
    ground_error = math.hypot(ground_obs.target_east_m - elevated_target[0], ground_obs.target_north_m - elevated_target[1])

    truth_wgs = frame.enu_to_wgs84(*true_target)
    metrics = geolocation_metrics([(estimate.target_id, estimate.east_m - true_target[0], estimate.north_m - true_target[1])])
    return {
        "calibration": calibration, "extrinsics": extrinsics, "clock_sync": clock.diagnostics(),
        "poses": poses, "observations": observations, "targets": [estimate],
        "ground_truth": {"synthetic": True, "targets": [{
            "target_id": estimate.target_id, "true_east_m": true_target[0], "true_north_m": true_target[1],
            "true_latitude": truth_wgs.latitude, "true_longitude": truth_wgs.longitude,
            "source": "SYNTHETIC_EXACT", "expected_accuracy_m": 0,
        }]},
        "metrics": {**metrics, "synthetic_zero_noise_error_m": zero_error,
                    "synthetic_noisy_median_error_m": noisy_center_error,
                    "synthetic_noisy_spread": monte_carlo,
                    "sensitivity": {"timestamp": timing, "attitude": attitude, "extrinsics": extrinsic,
                                    "gnss_common_bias_error_m": gnss_bias_error,
                                    "ground_height_2m_error_m": ground_error}},
        "origin": {"latitude": 17.45, "longitude": 78.38, "altitude_m": 500},
    }
