import json
from pathlib import Path

import numpy as np
import pytest

from rescue_core.geolocation.calibration import (
    CalibrationVerdict,
    CameraCalibration,
    CameraExtrinsics,
    DistortionModel,
    ExtrinsicsMethod,
    ExtrinsicsQuality,
)
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.ground import FlatGroundModel, RayGroundIntersector
from rescue_core.geolocation.transforms import quaternion_to_matrix


ROOT = Path(__file__).resolve().parents[1]


def test_python_matches_shared_stage11s_frontend_geometry_fixture() -> None:
    fixture = json.loads((ROOT / "data/fixtures/stage11s_spatial_geometry.json").read_text(encoding="utf-8"))
    source = fixture["calibration"]
    calibration = CameraCalibration(
        calibration_id=source["calibration_id"], camera_identifier="SYNTHETIC_TEST_ONLY",
        sensor="SYNTHETIC", resolution=tuple(source["resolution"]), sensor_mode=source["sensor_mode"],
        distortion_model=DistortionModel(source["distortion_model"]), camera_matrix=source["camera_matrix"],
        distortion_coefficients=source["distortion_coefficients"], image_width=source["image_width"],
        image_height=source["image_height"], rms_reprojection_error=0, mean_reprojection_error=0,
        median_reprojection_error=0, p95_reprojection_error=0, pattern_type="SYNTHETIC",
        pattern_dimensions=(9, 6), square_size_m=.025, images_used=0, images_rejected=0,
        software_version="Stage11S fixture", verdict=CalibrationVerdict.PASS,
        validation_reasons=["SYNTHETIC CROSS-LANGUAGE FIXTURE"],
    ).with_hash()
    ext = fixture["extrinsics"]
    extrinsics = CameraExtrinsics(
        extrinsics_id=ext["extrinsics_id"], camera_identifier="SYNTHETIC_TEST_ONLY",
        translation_body_m=ext["translation_body_m"], quaternion_camera_to_body=ext["quaternion_camera_to_body"],
        rotation_camera_to_body=quaternion_to_matrix(ext["quaternion_camera_to_body"]).tolist(),
        method=ExtrinsicsMethod.CALIBRATED, quality=ExtrinsicsQuality.CALIBRATED,
        rotation_uncertainty_deg=0, translation_uncertainty_m=0,
    )
    camera_ray = CameraModel.from_calibration(calibration).pixel_to_ray(tuple(fixture["pixel"]))
    assert camera_ray == pytest.approx(fixture["expected_camera_ray"], abs=1e-9)
    body_to_world = quaternion_to_matrix(fixture["quaternion_body_to_enu"])
    camera_to_body = quaternion_to_matrix(extrinsics.quaternion_camera_to_body)
    origin = np.asarray(fixture["vehicle_position_enu_m"], dtype=float) + body_to_world @ np.asarray(extrinsics.translation_body_m)
    world_ray = body_to_world @ camera_to_body @ camera_ray
    assert world_ray == pytest.approx(fixture["expected_world_ray_enu"], abs=1e-9)
    hit = RayGroundIntersector().intersect(origin, world_ray, FlatGroundModel(fixture["ground_up_m"]))
    assert hit.point_enu_m == pytest.approx(fixture["expected_intersection_enu_m"], abs=1e-9)
    assert hit.distance_m == pytest.approx(fixture["expected_range_m"], abs=1e-9)
