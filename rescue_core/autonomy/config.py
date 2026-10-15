"""Validated SITL-only engineering defaults for Stage 7S."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import SearchResumePolicyName


class TargetPolicyConfig(BaseModel):
    max_geolocation_uncertainty_m: float = Field(gt=0)
    priority_policy: str = "FIRST_CONFIRMED"
    minimum_battery_percent: float = Field(ge=0, le=100)


class GuidedConfig(BaseModel):
    approach_altitude_m: float = Field(gt=0)
    arrival_radius_m: float = Field(gt=0)
    arrival_altitude_tolerance_m: float = Field(gt=0)
    arrival_speed_m_s: float = Field(gt=0)
    arrival_dwell_s: float = Field(gt=0)
    max_approach_time_s: float = Field(gt=0)
    setpoint_rate_hz: float = Field(gt=1, le=20)


class ReacquisitionConfig(BaseModel):
    timeout_s: float = Field(gt=0)
    search_radius_m: float = Field(gt=0)
    standoff_m: float = Field(default=5.0, gt=0)
    search_pattern: str = "CROSS"


class AlignmentConfig(BaseModel):
    kp: float = Field(gt=0)
    max_horizontal_speed_m_s: float = Field(gt=0)
    deadband_m: float = Field(ge=0)
    alignment_radius_m: float = Field(gt=0)
    stable_dwell_s: float = Field(gt=0)
    observation_stale_s: float = Field(gt=0)
    target_pixel_y_ratio: float = Field(default=0.5, ge=0.5, lt=0.98)


class DeliveryConfig(BaseModel):
    delivery_altitude_m: float = Field(gt=0)
    visual_standoff_m: float = Field(default=2.6, gt=0)
    safe_drop_offset_m: float = Field(default=2.5, ge=1.0, le=10.0)
    max_descent_rate_m_s: float = Field(gt=0)
    max_horizontal_speed_at_drop_m_s: float = Field(gt=0)
    verification_latency_s: float = Field(ge=0)
    payload_slots: int = Field(default=2, ge=0, le=10)


class ResumeConfig(BaseModel):
    policy: SearchResumePolicyName = SearchResumePolicyName.ARDUPILOT_NATIVE_RESUME


class RescueSafetyConfig(BaseModel):
    operational_boundary_margin_m: float = Field(ge=0)
    guided_command_timeout_s: float = Field(gt=0)
    maximum_mission_duration_s: float = Field(gt=0)
    delivery_time_reserve_s: float = Field(ge=0)


class RescueOrchestrationConfig(BaseModel):
    execution_source: str
    engineering_defaults_only: bool
    target: TargetPolicyConfig
    guided: GuidedConfig
    reacquisition: ReacquisitionConfig
    alignment: AlignmentConfig
    delivery: DeliveryConfig
    resume: ResumeConfig
    safety: RescueSafetyConfig

    @model_validator(mode="after")
    def enforce_sitl(self) -> "RescueOrchestrationConfig":
        if self.execution_source != "sitl" or not self.engineering_defaults_only:
            raise ValueError("Stage 7S configuration must be explicitly SITL engineering-only")
        if self.delivery.delivery_altitude_m >= self.guided.approach_altitude_m:
            raise ValueError("Delivery altitude must be below approach altitude")
        return self


def load_rescue_config(path: str | Path = "config/rescue_orchestration.yaml") -> RescueOrchestrationConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RescueOrchestrationConfig.model_validate(raw)
