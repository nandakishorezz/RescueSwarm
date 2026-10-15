"""Validated Stage 5 configuration; all numerical thresholds are development defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class PoseConfig(BaseModel):
    history_seconds: float = Field(gt=0)
    max_interpolation_gap_ms: float = Field(gt=0)
    max_extrapolation_ms: float = Field(ge=0)


class ClockSyncConfig(BaseModel):
    enabled: bool = True
    minimum_samples: int = Field(ge=2)
    maximum_samples: int = Field(ge=3)
    max_rtt_ms: float = Field(gt=0)
    degraded_rtt_p95_ms: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "ClockSyncConfig":
        if self.minimum_samples > self.maximum_samples:
            raise ValueError("clock_sync.minimum_samples cannot exceed maximum_samples")
        return self


class GroundModelConfig(BaseModel):
    type: Literal["flat", "known_elevation"] = "flat"
    source: str
    ground_up_m: float = 0.0


class QualityConfig(BaseModel):
    require_calibration: bool = True
    require_pose: bool = True
    require_valid_gps: bool = True
    minimum_gps_fix: int = Field(ge=0, le=8)
    maximum_view_angle_from_nadir_deg: float = Field(gt=0, lt=90)
    mission_region_tolerance_m: float = Field(ge=0)


class UncertaintyConfig(BaseModel):
    horizontal_position_prior_m: float = Field(gt=0)
    vertical_position_prior_m: float = Field(gt=0)
    pixel_prior_px: float = Field(gt=0)
    attitude_prior_deg: float = Field(gt=0)
    mount_prior_deg: float = Field(gt=0)
    minimum_reported_uncertainty_m: float = Field(gt=0)


class ConfirmationConfig(BaseModel):
    minimum_observations: int = Field(ge=2)
    minimum_time_span_s: float = Field(ge=0)
    time_window_s: float = Field(gt=0)
    association_radius_m: float = Field(gt=0)
    short_term_time_s: float = Field(gt=0)
    short_term_pixel_radius_px: float = Field(gt=0)
    minimum_detection_confidence: float = Field(ge=0, le=1)
    maximum_geolocation_uncertainty_m: float = Field(gt=0)


class FusionConfig(BaseModel):
    method: Literal["robust_weighted"] = "robust_weighted"
    outlier_mad_scale: float = Field(gt=0)
    minimum_outlier_gate_m: float = Field(gt=0)
    minimum_uncertainty_m: float = Field(gt=0)


class WorkerConfig(BaseModel):
    queue_size: int = Field(ge=1, le=10000)
    drop_policy: Literal["drop_oldest"] = "drop_oldest"


class GeolocationSettings(BaseModel):
    calibration_id: str | None = None
    extrinsics_id: str | None = None
    target_pixel_strategy: Literal["BBOX_CENTER", "BBOX_BOTTOM_CENTER"] = "BBOX_BOTTOM_CENTER"
    pose: PoseConfig
    clock_sync: ClockSyncConfig
    ground_model: GroundModelConfig
    quality: QualityConfig
    uncertainty: UncertaintyConfig
    confirmation: ConfirmationConfig
    fusion: FusionConfig
    worker: WorkerConfig


class Stage5Config(BaseModel):
    application: dict[str, object]
    geolocation: GeolocationSettings


def load_geolocation_config(path: str | Path = "config/geolocation.yaml") -> Stage5Config:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Geolocation configuration does not exist: {config_path}")
    return Stage5Config.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
