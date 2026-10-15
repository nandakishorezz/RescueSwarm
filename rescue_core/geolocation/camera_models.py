"""Calibrated projection/unprojection for pinhole-Brown and fisheye lenses."""

from __future__ import annotations

from abc import ABC, abstractmethod

import cv2
import numpy as np

from .calibration import CameraCalibration, DistortionModel
from .transforms import normalize_vector


class CameraModel(ABC):
    def __init__(self, calibration: CameraCalibration):
        self.calibration = calibration
        self.matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
        self.distortion = np.asarray(calibration.distortion_coefficients, dtype=np.float64)

    @abstractmethod
    def pixel_to_ray(self, pixel: tuple[float, float]) -> np.ndarray:
        """Return a normalized CAMERA_OPTICAL ray (+z forward)."""

    @abstractmethod
    def ray_to_pixel(self, ray_camera: np.ndarray) -> tuple[float, float] | None:
        """Project a camera ray; return None if it points behind the imager."""

    @classmethod
    def from_calibration(cls, calibration: CameraCalibration) -> "CameraModel":
        if calibration.distortion_model is DistortionModel.PINHOLE_BROWN:
            return PinholeBrownCameraModel(calibration)
        if calibration.distortion_model is DistortionModel.FISHEYE:
            return FisheyeCameraModel(calibration)
        raise ValueError(f"Unsupported distortion model: {calibration.distortion_model}")

    def in_image(self, pixel: tuple[float, float]) -> bool:
        x, y = pixel
        return 0 <= x < self.calibration.image_width and 0 <= y < self.calibration.image_height


class PinholeBrownCameraModel(CameraModel):
    def pixel_to_ray(self, pixel: tuple[float, float]) -> np.ndarray:
        points = np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2)
        undistorted = cv2.undistortPoints(points, self.matrix, self.distortion).reshape(2)
        return normalize_vector(np.array([undistorted[0], undistorted[1], 1.0]))

    def ray_to_pixel(self, ray_camera: np.ndarray) -> tuple[float, float] | None:
        ray = np.asarray(ray_camera, dtype=np.float64)
        if ray.shape != (3,) or ray[2] <= 1e-12:
            return None
        points, _ = cv2.projectPoints(
            ray.reshape(1, 1, 3), np.zeros(3), np.zeros(3), self.matrix, self.distortion
        )
        x, y = points.reshape(2)
        return float(x), float(y)


class FisheyeCameraModel(CameraModel):
    def pixel_to_ray(self, pixel: tuple[float, float]) -> np.ndarray:
        points = np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2)
        undistorted = cv2.fisheye.undistortPoints(points, self.matrix, self.distortion[:4]).reshape(2)
        return normalize_vector(np.array([undistorted[0], undistorted[1], 1.0]))

    def ray_to_pixel(self, ray_camera: np.ndarray) -> tuple[float, float] | None:
        ray = np.asarray(ray_camera, dtype=np.float64)
        if ray.shape != (3,) or ray[2] <= 1e-12:
            return None
        normalized = (ray / ray[2])[:2].reshape(1, 1, 2)
        projected = cv2.fisheye.distortPoints(normalized, self.matrix, self.distortion[:4]).reshape(2)
        return float(projected[0]), float(projected[1])

