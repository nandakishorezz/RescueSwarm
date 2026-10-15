"""Small, testable detection-pixel to mission-ENU ground localization chain."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from rescue_core.vision.models import Detection, DetectionResult

from .calibration import CameraCalibration, CameraExtrinsics, ExtrinsicsQuality
from .camera_models import CameraModel
from .clock_sync import ClockSyncEstimator, ClockSyncQuality
from .ground import GroundModel, RayGroundIntersector
from .models import RejectedObservation, TargetObservation, TargetPixelStrategy
from .pose import PoseHistory
from .transforms import body_to_enu_rotation, ned_to_enu, normalize_vector
from .uncertainty import GeolocationUncertaintyEstimator


Wgs84Converter = Callable[[float, float, float], Any]
MissionRegionValidator = Callable[[float, float], bool]


class TargetGeolocator:
    def __init__(
        self, *, calibration: CameraCalibration, extrinsics: CameraExtrinsics,
        pose_history: PoseHistory, clock_sync: ClockSyncEstimator | None,
        ground_model: GroundModel, wgs84_converter: Wgs84Converter,
        uncertainty: GeolocationUncertaintyEstimator,
        pixel_strategy: TargetPixelStrategy = TargetPixelStrategy.BBOX_BOTTOM_CENTER,
        minimum_gps_fix: int = 3, maximum_view_angle_from_nadir_deg: float = 70,
        require_sync: bool = True, require_calibrated_extrinsics: bool = False,
        mission_region_validator: MissionRegionValidator | None = None,
    ):
        calibration.verify_hash()
        extrinsics.verify_hash()
        self.calibration = calibration
        self.extrinsics = extrinsics
        self.camera_model = CameraModel.from_calibration(calibration)
        self.pose_history = pose_history
        self.clock_sync = clock_sync
        self.ground_model = ground_model
        self.wgs84_converter = wgs84_converter
        self.uncertainty = uncertainty
        self.pixel_strategy = pixel_strategy
        self.minimum_gps_fix = minimum_gps_fix
        self.maximum_view_angle = maximum_view_angle_from_nadir_deg
        self.require_sync = require_sync
        self.require_calibrated_extrinsics = require_calibrated_extrinsics
        self.mission_region_validator = mission_region_validator
        self.intersector = RayGroundIntersector()

    def geolocate(
        self, result: DetectionResult, detection: Detection, vehicle_id: str,
        *, active_width: int, active_height: int, active_sensor_mode: str,
        mapped_autopilot_time_ns: int | None = None,
        evidence_references: list[str] | None = None,
    ) -> TargetObservation | RejectedObservation:
        try:
            self.calibration.assert_compatible(active_width, active_height, active_sensor_mode)
            if self.require_calibrated_extrinsics and self.extrinsics.quality is not ExtrinsicsQuality.CALIBRATED:
                raise ValueError("EXTRINSICS_NOT_CALIBRATED")
            if self.require_sync and (not self.clock_sync or self.clock_sync.quality is not ClockSyncQuality.SYNCED):
                raise ValueError("CLOCK_UNSYNCED")
            if mapped_autopilot_time_ns is not None:
                interpolated = self.pose_history.lookup_autopilot_time(
                    mapped_autopilot_time_ns, clock_sync_quality=ClockSyncQuality.SYNCED,
                )
            else:
                interpolated = self.pose_history.lookup(result.capture_timestamp_monotonic_ns, self.clock_sync)
            pose = interpolated.sample
            if pose.local_position_ned_m is None:
                raise ValueError("POSE_UNAVAILABLE: local position NED missing")
            if pose.gps_fix is None or pose.gps_fix < self.minimum_gps_fix:
                raise ValueError("GPS_INVALID")
            pixel = self._chosen_pixel(detection)
            camera_ray = self.camera_model.pixel_to_ray(pixel)
            camera_to_body = np.asarray(self.extrinsics.rotation_camera_to_body, dtype=float)
            body_to_enu = body_to_enu_rotation(pose.quaternion_body_to_ned)
            world_ray = normalize_vector(body_to_enu @ camera_to_body @ camera_ray)
            vehicle_origin = ned_to_enu(pose.local_position_ned_m)
            camera_origin = vehicle_origin + body_to_enu @ np.asarray(self.extrinsics.translation_body_m)
            nadir_cosine = float(np.dot(world_ray, np.array([0.0, 0.0, -1.0])))
            view_angle = math.degrees(math.acos(max(-1.0, min(1.0, nadir_cosine))))
            if view_angle > self.maximum_view_angle:
                raise ValueError("VIEW_ANGLE_TOO_SHALLOW")
            intersection = self.intersector.intersect(camera_origin, world_ray, self.ground_model)
            target = intersection.point_enu_m
            if self.mission_region_validator and not self.mission_region_validator(float(target[0]), float(target[1])):
                raise ValueError("TARGET_OUTSIDE_MISSION_REGION")
            wgs84 = self.wgs84_converter(float(target[0]), float(target[1]), float(target[2]))
            focal = (self.calibration.camera_matrix[0][0] + self.calibration.camera_matrix[1][1]) / 2
            estimate = self.uncertainty.estimate(
                pose=interpolated, calibration=self.calibration, extrinsics=self.extrinsics,
                range_m=intersection.distance_m, view_angle_from_nadir_deg=view_angle,
                focal_length_px=focal, ground_source=self.ground_model.source,
            )
            return TargetObservation(
                observation_id=str(uuid5(
                    NAMESPACE_URL,
                    f"{vehicle_id}:{result.frame_id}:{detection.x1:.6f}:{detection.y1:.6f}:"
                    f"{detection.x2:.6f}:{detection.y2:.6f}:{self.calibration.calibration_id}",
                )),
                vehicle_id=vehicle_id, frame_id=result.frame_id,
                capture_timestamp_monotonic_ns=result.capture_timestamp_monotonic_ns,
                capture_timestamp_wall_ns=result.capture_timestamp_wall_ns,
                detection_confidence=detection.confidence,
                bbox_original_px=(detection.x1, detection.y1, detection.x2, detection.y2),
                chosen_pixel=pixel, pixel_strategy=self.pixel_strategy,
                camera_calibration_id=self.calibration.calibration_id,
                extrinsics_id=self.extrinsics.extrinsics_id,
                interpolated_pose=interpolated,
                camera_ray=tuple(float(value) for value in camera_ray),
                world_ray_enu=tuple(float(value) for value in world_ray),
                camera_origin_enu_m=tuple(float(value) for value in camera_origin),
                target_east_m=float(target[0]), target_north_m=float(target[1]), target_up_m=float(target[2]),
                target_latitude=float(wgs84.latitude), target_longitude=float(wgs84.longitude),
                range_m=intersection.distance_m, view_angle_from_nadir_deg=view_angle,
                altitude_source="LOCAL_POSITION_NED+KNOWN_GROUND_MODEL",
                position_uncertainty_m=estimate.uncertainty_m,
                uncertainty_components=estimate.components, quality=estimate.quality,
                evidence_references=evidence_references or [],
            )
        except (ValueError, ArithmeticError) as exc:
            return RejectedObservation(
                vehicle_id=vehicle_id, frame_id=result.frame_id,
                capture_timestamp_monotonic_ns=result.capture_timestamp_monotonic_ns,
                reason=str(exc), camera_calibration_id=self.calibration.calibration_id,
                extrinsics_id=self.extrinsics.extrinsics_id,
            )

    def _chosen_pixel(self, detection: Detection) -> tuple[float, float]:
        if self.pixel_strategy is TargetPixelStrategy.BBOX_CENTER:
            return detection.center_x, detection.center_y
        return detection.bottom_center_x, detection.bottom_center_y
