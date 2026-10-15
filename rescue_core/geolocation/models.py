"""Normalized Stage 5 observations, fused targets, health, and evidence models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .pose import InterpolatedPose


class TargetPixelStrategy(StrEnum):
    BBOX_CENTER = "BBOX_CENTER"
    BBOX_BOTTOM_CENTER = "BBOX_BOTTOM_CENTER"


class GeolocationQuality(StrEnum):
    INVALID = "INVALID"
    LOW = "LOW"
    STANDARD_GNSS_LIMITED = "STANDARD_GNSS_LIMITED"
    GOOD = "GOOD"
    PRECISION = "PRECISION"


class TargetTrackState(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class TargetObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    target_track_id: str | None = None
    vehicle_id: str
    frame_id: str
    capture_timestamp_monotonic_ns: int
    capture_timestamp_wall_ns: int | None = None
    detection_confidence: float = Field(ge=0, le=1)
    bbox_original_px: tuple[float, float, float, float]
    chosen_pixel: tuple[float, float]
    pixel_strategy: TargetPixelStrategy
    camera_calibration_id: str
    extrinsics_id: str
    interpolated_pose: InterpolatedPose
    camera_ray: tuple[float, float, float]
    world_ray_enu: tuple[float, float, float]
    camera_origin_enu_m: tuple[float, float, float]
    target_east_m: float
    target_north_m: float
    target_up_m: float
    target_latitude: float
    target_longitude: float
    range_m: float = Field(gt=0)
    altitude_source: str
    view_angle_from_nadir_deg: float = Field(ge=0, le=180)
    position_uncertainty_m: float = Field(gt=0)
    uncertainty_components: dict[str, float | str | None] = Field(default_factory=dict)
    quality: GeolocationQuality
    evidence_references: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RejectedObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    vehicle_id: str
    frame_id: str
    capture_timestamp_monotonic_ns: int
    reason: str
    camera_calibration_id: str | None = None
    extrinsics_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    quality: GeolocationQuality = GeolocationQuality.INVALID


class FusedTargetEstimate(BaseModel):
    target_id: str = Field(default_factory=lambda: str(uuid4()))
    mission_id: str
    state: TargetTrackState
    east_m: float
    north_m: float
    latitude: float
    longitude: float
    uncertainty_m: float = Field(gt=0)
    detection_confidence: float = Field(ge=0, le=1)
    observation_count: int = Field(ge=1)
    inlier_count: int = Field(ge=1)
    outlier_count: int = Field(ge=0)
    first_seen_at: datetime
    last_seen_at: datetime
    assigned_vehicle_id: str | None = None
    delivery_state: str = "PENDING"
    evidence_references: list[str] = Field(default_factory=list)
    inlier_observation_ids: list[str] = Field(default_factory=list)
    outlier_observation_ids: list[str] = Field(default_factory=list)
    camera_calibration_ids: list[str] = Field(default_factory=list)
    extrinsics_ids: list[str] = Field(default_factory=list)
    quality: GeolocationQuality
    simulated: bool = False


class GroundTruthSource(StrEnum):
    SYNTHETIC_EXACT = "SYNTHETIC_EXACT"
    SURVEYED = "SURVEYED"
    SURVEYED_GNSS = "SURVEYED_GNSS"
    RTK = "RTK"
    MAP = "MAP"
    MANUAL = "MANUAL"
    TAPE_MEASURED_LOCAL = "TAPE_MEASURED_LOCAL"
    LASER_MEASURED_LOCAL = "LASER_MEASURED_LOCAL"
    STANDARD_GPS = "STANDARD_GPS"
    MANUAL_MAP_REFERENCE = "MANUAL_MAP_REFERENCE"
    UNKNOWN = "UNKNOWN"


class GroundTruthTarget(BaseModel):
    target_id: str
    true_latitude: float | None = Field(default=None, ge=-90, le=90)
    true_longitude: float | None = Field(default=None, ge=-180, le=180)
    east_m: float | None = None
    north_m: float | None = None
    source: GroundTruthSource
    expected_accuracy_m: float | None = Field(default=None, ge=0)
    notes: str = ""

    @model_validator(mode="after")
    def require_coordinate_pair(self) -> "GroundTruthTarget":
        local = self.east_m is not None or self.north_m is not None
        geographic = self.true_latitude is not None or self.true_longitude is not None
        if local and (self.east_m is None or self.north_m is None):
            raise ValueError("east_m and north_m must be supplied together")
        if geographic and (self.true_latitude is None or self.true_longitude is None):
            raise ValueError("true_latitude and true_longitude must be supplied together")
        if not local and not geographic:
            raise ValueError("ground truth requires local or geographic coordinates")
        return self


class GeolocationHealthState(StrEnum):
    CALIBRATION_MISSING = "CALIBRATION_MISSING"
    CALIBRATION_MISMATCH = "CALIBRATION_MISMATCH"
    CLOCK_UNSYNCED = "CLOCK_UNSYNCED"
    POSE_STALE = "POSE_STALE"
    GPS_INVALID = "GPS_INVALID"
    GEOLOCATION_READY = "GEOLOCATION_READY"
    FAILED = "FAILED"


class CalibrationHealth(StrEnum):
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    READY = "READY"


class PoseSyncHealth(StrEnum):
    EMPTY = "EMPTY"
    READY = "READY"
    STALE = "STALE"
    GAP_TOO_LARGE = "GAP_TOO_LARGE"


class GeolocationHealth(BaseModel):
    state: GeolocationHealthState
    ready: bool
    reason: str
    clock_sync: str
    calibration_id: str | None = None
    extrinsics_id: str | None = None
    last_observation_at: datetime | None = None
