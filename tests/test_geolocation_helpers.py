"""Shared deterministic Stage 5 fixtures; synthetic evidence is never physical evidence."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from rescue_core.geolocation.calibration import (
    CalibrationVerdict,
    CameraCalibration,
    CameraExtrinsics,
    DistortionModel,
    ExtrinsicsMethod,
    ExtrinsicsQuality,
)
from rescue_core.geolocation.clock_sync import ClockSyncEstimator
from rescue_core.geolocation.pose import PoseHistory, PoseSample


def synthetic_calibration(model: DistortionModel = DistortionModel.PINHOLE_BROWN) -> CameraCalibration:
    coefficients = [0.01, -0.005, 0.0002, -0.0001, 0.001] if model is DistortionModel.PINHOLE_BROWN else [0.01, -0.005, 0.001, -0.0002]
    return CameraCalibration(
        calibration_id=f"synthetic_{model.value.lower()}", camera_identifier="TEST_ONLY",
        sensor="SYNTHETIC", resolution=(1280, 800), sensor_mode="SYNTHETIC_1280x800",
        distortion_model=model, camera_matrix=[[800, 0, 640], [0, 800, 400], [0, 0, 1]],
        distortion_coefficients=coefficients, image_width=1280, image_height=800,
        rms_reprojection_error=0.1, mean_reprojection_error=0.08,
        median_reprojection_error=0.07, p95_reprojection_error=0.15,
        pattern_type="SYNTHETIC", pattern_dimensions=(9, 6), square_size_m=0.025,
        images_used=20, images_rejected=0, software_version="TEST_ONLY",
        verdict=CalibrationVerdict.PASS,
    ).with_hash()


def nadir_extrinsics(quality: ExtrinsicsQuality = ExtrinsicsQuality.CALIBRATED) -> CameraExtrinsics:
    # Columns map camera right/down/forward into body FRD right/backward/down.
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    return CameraExtrinsics.from_rotation_matrix(
        extrinsics_id="synthetic_nadir", camera_identifier="TEST_ONLY",
        translation_body_m=(0, 0, 0), rotation_camera_to_body=rotation,
        method=ExtrinsicsMethod.CALIBRATED, quality=quality,
        rotation_uncertainty_deg=0.01, translation_uncertainty_m=0.001,
        notes="SYNTHETIC TEST ONLY",
    )


def pose_sample(remote_ns: int, host_ns: int, north: float = 0, east: float = 0, up: float = 20) -> PoseSample:
    return PoseSample(
        vehicle_id="drone_01", autopilot_boot_time_ns=remote_ns,
        host_monotonic_time_ns=host_ns, host_wall_time_ns=host_ns + 1_700_000_000_000_000_000,
        latitude=17.45, longitude=78.38, absolute_altitude_m=500 + up,
        relative_altitude_m=up, local_position_ned_m=(north, east, -up),
        quaternion_body_to_ned=(1, 0, 0, 0), velocity_ned_m_s=(4, 0, 0),
        gps_fix=3, satellites=12, horizontal_accuracy_m=2.0, vertical_accuracy_m=3.0,
        accuracy_sources={"horizontal": "SYNTHETIC_TELEMETRY", "vertical": "SYNTHETIC_TELEMETRY"},
        source_message_ids=["SYNTHETIC_TEST"],
    )


def synced_history() -> tuple[PoseHistory, ClockSyncEstimator, int]:
    history = PoseHistory(history_seconds=10, max_interpolation_gap_ms=500, max_extrapolation_ms=20)
    clock = ClockSyncEstimator(minimum_samples=3, maximum_samples=20, max_rtt_ms=20, degraded_rtt_p95_ms=10)
    offset = 5_000_000_000
    for index in range(5):
        sent = 1_000_000_000 + index * 10_000_000
        clock.observe_response(sent, sent + 1_000_000 + offset, sent + 2_000_000)
    for index in range(3):
        host = 2_000_000_000 + index * 100_000_000
        history.add(pose_sample(host + offset, host, north=index * 0.4))
    return history, clock, offset


def coordinate_frame() -> LocalCoordinateFrame:
    return LocalCoordinateFrame(GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500))

