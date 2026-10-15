"""Independent test-only forward projector for known virtual ground targets."""

from __future__ import annotations

import numpy as np

from .calibration import CameraExtrinsics
from .camera_models import CameraModel
from .pose import PoseSample
from .transforms import body_to_enu_rotation, ned_to_enu


class SyntheticTargetProjector:
    """Forward projection used only for mathematical/SITL validation."""

    def __init__(self, camera_model: CameraModel, extrinsics: CameraExtrinsics):
        self.camera_model = camera_model
        self.extrinsics = extrinsics

    def project(self, target_enu_m: np.ndarray, pose: PoseSample) -> tuple[float, float] | None:
        if pose.local_position_ned_m is None:
            raise ValueError("Synthetic projection requires local_position_ned_m")
        body_to_enu = body_to_enu_rotation(pose.quaternion_body_to_ned)
        camera_to_body = np.asarray(self.extrinsics.rotation_camera_to_body, dtype=float)
        camera_origin = ned_to_enu(pose.local_position_ned_m) + body_to_enu @ np.asarray(self.extrinsics.translation_body_m)
        ray_enu = np.asarray(target_enu_m, dtype=float) - camera_origin
        ray_body = body_to_enu.T @ ray_enu
        ray_camera = camera_to_body.T @ ray_body
        pixel = self.camera_model.ray_to_pixel(ray_camera)
        return pixel if pixel is not None and self.camera_model.in_image(pixel) else None

