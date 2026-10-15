from __future__ import annotations

import math

import numpy as np
import pytest

from rescue_core.geolocation.calibration import DistortionModel
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.ground import FlatGroundModel, RayGroundIntersector
from rescue_core.geolocation.transforms import (
    body_to_enu_rotation,
    enu_to_ned,
    ned_to_enu,
    quaternion_from_euler,
    quaternion_slerp,
    rotate_vector,
)

from .test_geolocation_helpers import synthetic_calibration


@pytest.mark.parametrize("model", [DistortionModel.PINHOLE_BROWN, DistortionModel.FISHEYE])
def test_camera_principal_point_and_projection_round_trip(model: DistortionModel) -> None:
    camera = CameraModel.from_calibration(synthetic_calibration(model))
    assert camera.pixel_to_ray((640, 400)) == pytest.approx([0, 0, 1], abs=1e-9)
    ray = np.array([0.15, -0.08, 1.0])
    pixel = camera.ray_to_pixel(ray)
    assert pixel is not None
    restored = camera.pixel_to_ray(pixel)
    assert restored == pytest.approx(ray / np.linalg.norm(ray), abs=2e-6)


def test_ned_enu_axis_gate_and_rotation() -> None:
    assert ned_to_enu([1, 0, 0]) == pytest.approx([0, 1, 0])  # North remains North.
    assert ned_to_enu([0, 1, 0]) == pytest.approx([1, 0, 0])  # East remains East.
    assert ned_to_enu([0, 0, 1]) == pytest.approx([0, 0, -1])  # Down is negative Up.
    vector = np.array([4, -3, 2])
    assert enu_to_ned(ned_to_enu(vector)) == pytest.approx(vector)
    yaw_east = quaternion_from_euler(0, 0, math.pi / 2)
    assert body_to_enu_rotation(yaw_east) @ np.array([1, 0, 0]) == pytest.approx([1, 0, 0], abs=1e-8)


def test_quaternion_slerp_handles_yaw_wraparound() -> None:
    first = quaternion_from_euler(0, 0, math.radians(359))
    second = quaternion_from_euler(0, 0, math.radians(1))
    midpoint = quaternion_slerp(first, second, 0.5)
    assert rotate_vector(midpoint, [1, 0, 0]) == pytest.approx([1, 0, 0], abs=1e-5)


def test_ray_ground_intersection_guards_geometry() -> None:
    intersector = RayGroundIntersector()
    intersection = intersector.intersect(np.array([5, 7, 20]), np.array([0, 0, -1]), FlatGroundModel())
    assert intersection.point_enu_m == pytest.approx([5, 7, 0])
    assert intersection.distance_m == pytest.approx(20)
    offset = intersector.intersect(np.array([0, 0, 20]), np.array([0.1, 0, -1]), FlatGroundModel())
    assert offset.point_enu_m[0] == pytest.approx(2)
    with pytest.raises(ValueError, match="upward or is parallel"):
        intersector.intersect(np.array([0, 0, 20]), np.array([0, 0, 1]), FlatGroundModel())
    with pytest.raises(ValueError, match="upward or is parallel"):
        intersector.intersect(np.array([0, 0, 20]), np.array([1, 0, 0]), FlatGroundModel())

