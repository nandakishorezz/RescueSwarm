"""Versioned Stage 13SIM profiles with explicit physical-value provenance."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class Provenance(StrEnum):
    PHYSICAL_MEASURED = "PHYSICAL_MEASURED"
    PHYSICAL_UNVERIFIED = "PHYSICAL_UNVERIFIED"
    REFERENCE_PRESET = "REFERENCE_PRESET"
    ENGINEERING_ESTIMATE = "ENGINEERING_ESTIMATE"
    SIMULATION_ONLY = "SIMULATION_ONLY"


class ScalarValue(BaseModel):
    value: float
    unit: str = Field(min_length=1)
    provenance: Provenance


class Vector3Value(BaseModel):
    value: tuple[float, float, float]
    unit: str = Field(min_length=1)
    provenance: Provenance


class QuaternionValue(BaseModel):
    """Scalar-first quaternion [w, x, y, z]."""

    value: tuple[float, float, float, float]
    provenance: Provenance

    @model_validator(mode="after")
    def normalized(self) -> "QuaternionValue":
        norm = math.sqrt(sum(component * component for component in self.value))
        if not math.isfinite(norm) or norm <= 1e-9:
            raise ValueError("camera mount quaternion must be finite and non-zero")
        if abs(norm - 1.0) > 1e-6:
            raise ValueError("camera mount quaternion must be normalized")
        return self


class RotorLayout(StrEnum):
    QUAD_X = "QUAD_X"
    QUAD_PLUS = "QUAD_PLUS"
    HEXA_X = "HEXA_X"
    OCTA_X = "OCTA_X"
    CUSTOM = "CUSTOM"


class RotationDirection(StrEnum):
    CW = "CW"
    CCW = "CCW"


LAYOUT_COUNTS = {
    RotorLayout.QUAD_X: 4,
    RotorLayout.QUAD_PLUS: 4,
    RotorLayout.HEXA_X: 6,
    RotorLayout.OCTA_X: 8,
}


class InertiaTensor(BaseModel):
    ixx: ScalarValue
    iyy: ScalarValue
    izz: ScalarValue
    ixy: ScalarValue
    ixz: ScalarValue
    iyz: ScalarValue

    @model_validator(mode="after")
    def physically_valid(self) -> "InertiaTensor":
        diagonal = (self.ixx.value, self.iyy.value, self.izz.value)
        if any(not math.isfinite(value) or value <= 0 for value in diagonal):
            raise ValueError("inertia diagonal values must be positive and finite")
        if self.ixx.value + self.iyy.value < self.izz.value or self.ixx.value + self.izz.value < self.iyy.value or self.iyy.value + self.izz.value < self.ixx.value:
            raise ValueError("inertia tensor violates the rigid-body triangle inequality")
        return self


class ThrustModel(BaseModel):
    motor_constant: ScalarValue
    moment_constant: ScalarValue
    max_rotor_velocity: ScalarValue


class RotorSpec(BaseModel):
    rotor_id: str
    position_body_frd: Vector3Value
    thrust_axis_body_frd: Vector3Value
    rotation_direction: RotationDirection
    diameter: ScalarValue
    thrust_model: ThrustModel
    motor_time_constant: ScalarValue

    @model_validator(mode="after")
    def valid_rotor(self) -> "RotorSpec":
        if self.diameter.value <= 0 or self.motor_time_constant.value <= 0:
            raise ValueError("rotor diameter and motor time constant must be positive")
        axis_norm = math.sqrt(sum(component * component for component in self.thrust_axis_body_frd.value))
        if abs(axis_norm - 1.0) > 1e-6:
            raise ValueError("rotor thrust axis must be a unit vector")
        return self


class DragParameters(BaseModel):
    linear_coefficient: ScalarValue
    angular_coefficient: ScalarValue


class DroneProfile(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: str
    vehicle_name: str
    frame_type: Literal["MULTIROTOR"] = "MULTIROTOR"
    rotor_count: int = Field(ge=1, le=16)
    rotor_layout: RotorLayout
    mass_kg: ScalarValue
    body_length_m: ScalarValue
    body_width_m: ScalarValue
    body_height_m: ScalarValue
    arm_length_m: ScalarValue
    center_of_mass: Vector3Value
    inertia: InertiaTensor
    rotors: list[RotorSpec]
    drag_parameters: DragParameters
    payload_mass_kg: ScalarValue
    battery_mass_kg: ScalarValue
    max_commanded_speed_mps: ScalarValue
    cruise_speed_mps: ScalarValue
    acceleration_limit_mps2: ScalarValue

    @model_validator(mode="after")
    def valid_profile(self) -> "DroneProfile":
        positive = {
            "mass": self.mass_kg.value,
            "body length": self.body_length_m.value,
            "body width": self.body_width_m.value,
            "body height": self.body_height_m.value,
            "arm length": self.arm_length_m.value,
            "maximum speed": self.max_commanded_speed_mps.value,
            "cruise speed": self.cruise_speed_mps.value,
            "acceleration": self.acceleration_limit_mps2.value,
        }
        invalid = [name for name, value in positive.items() if not math.isfinite(value) or value <= 0]
        if invalid:
            raise ValueError("drone profile values must be positive: " + ", ".join(invalid))
        expected = LAYOUT_COUNTS.get(self.rotor_layout)
        if expected is not None and self.rotor_count != expected:
            raise ValueError(f"{self.rotor_layout} requires {expected} rotors")
        if self.rotor_count != len(self.rotors):
            raise ValueError("rotor_count must match the generated rotor list")
        positions = [tuple(round(value, 9) for value in rotor.position_body_frd.value) for rotor in self.rotors]
        if len(set(positions)) != len(positions):
            raise ValueError("rotor positions must be unique")
        if self.cruise_speed_mps.value > self.max_commanded_speed_mps.value:
            raise ValueError("cruise speed cannot exceed maximum commanded speed")
        return self


class CameraIntrinsics(BaseModel):
    fx: ScalarValue
    fy: ScalarValue
    cx: ScalarValue
    cy: ScalarValue

    @model_validator(mode="after")
    def valid_intrinsics(self) -> "CameraIntrinsics":
        if self.fx.value <= 0 or self.fy.value <= 0:
            raise ValueError("camera focal lengths must be positive")
        return self


class CameraProfile(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    camera_id: str
    sensor_name: str
    resolution_width: int = Field(gt=0, le=16384)
    resolution_height: int = Field(gt=0, le=16384)
    fps: ScalarValue
    intrinsics: CameraIntrinsics | None = None
    distortion_model: Literal["PINHOLE_BROWN", "FISHEYE", "NONE"] = "PINHOLE_BROWN"
    distortion_coefficients: list[float] = Field(default_factory=list, max_length=12)
    horizontal_fov: ScalarValue | None = None
    vertical_fov: ScalarValue | None = None
    mount_translation_body_frd: Vector3Value
    mount_rotation_camera_to_body: QuaternionValue
    mount_roll_deg: ScalarValue
    mount_pitch_deg: ScalarValue
    mount_yaw_deg: ScalarValue
    exposure_s: ScalarValue
    latency_ms: ScalarValue
    noise_profile: str = "NONE"

    @model_validator(mode="after")
    def valid_camera(self) -> "CameraProfile":
        if self.fps.value <= 0 or self.exposure_s.value < 0 or self.latency_ms.value < 0:
            raise ValueError("camera FPS must be positive and exposure/latency non-negative")
        if self.intrinsics is None and self.horizontal_fov is None:
            raise ValueError("camera requires explicit intrinsics or horizontal FOV")
        if self.intrinsics is not None:
            if not 0 <= self.intrinsics.cx.value < self.resolution_width:
                raise ValueError("camera cx must lie within the configured image")
            if not 0 <= self.intrinsics.cy.value < self.resolution_height:
                raise ValueError("camera cy must lie within the configured image")
        for fov in (self.horizontal_fov, self.vertical_fov):
            if fov is not None and not 0 < fov.value < math.pi:
                raise ValueError("camera FOV values must be in radians and within (0, pi)")
        return self

    def projective_intrinsics(self) -> tuple[float, float, float, float, Provenance]:
        if self.intrinsics:
            return (
                self.intrinsics.fx.value, self.intrinsics.fy.value,
                self.intrinsics.cx.value, self.intrinsics.cy.value,
                self.intrinsics.fx.provenance,
            )
        assert self.horizontal_fov is not None
        fx = self.resolution_width / (2 * math.tan(self.horizontal_fov.value / 2))
        vertical = self.vertical_fov.value if self.vertical_fov else 2 * math.atan(self.resolution_height / (2 * fx))
        fy = self.resolution_height / (2 * math.tan(vertical / 2))
        return fx, fy, self.resolution_width / 2, self.resolution_height / 2, Provenance.ENGINEERING_ESTIMATE

    def derived_fov(self) -> tuple[float, float]:
        fx, fy, _, _, _ = self.projective_intrinsics()
        return (
            2 * math.atan(self.resolution_width / (2 * fx)),
            2 * math.atan(self.resolution_height / (2 * fy)),
        )


class WindMode(StrEnum):
    CALM = "CALM"
    CONSTANT = "CONSTANT"
    TURBULENT = "TURBULENT"
    GUSTING = "GUSTING"


class WindProfile(BaseModel):
    enabled: bool = True
    mode: WindMode = WindMode.CONSTANT
    mean_speed_mps: ScalarValue
    mean_direction_deg: ScalarValue
    vertical_speed_mps: ScalarValue
    turbulence_enabled: bool = False
    turbulence_intensity: ScalarValue
    direction_variation_deg: ScalarValue
    gusts_enabled: bool = False
    gust_max_speed_mps: ScalarValue
    gust_min_duration_s: ScalarValue
    gust_max_duration_s: ScalarValue
    random_seed: int

    @model_validator(mode="after")
    def valid_wind(self) -> "WindProfile":
        nonnegative = (self.mean_speed_mps.value, self.turbulence_intensity.value, self.direction_variation_deg.value, self.gust_max_speed_mps.value)
        if any(value < 0 or not math.isfinite(value) for value in nonnegative):
            raise ValueError("wind speed, turbulence, variation, and gust values must be non-negative")
        if self.gust_min_duration_s.value <= 0 or self.gust_max_duration_s.value < self.gust_min_duration_s.value:
            raise ValueError("wind gust duration range is invalid")
        return self


class EnvironmentProfile(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: str
    gravity: Vector3Value
    ground: str = "FLAT"
    lighting: str = "DAY"
    wind: WindProfile
    random_seed: int
    real_time_factor: float = Field(default=1.0, gt=0, le=20)


class MissionWorldProfile(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    mission_id: str
    boundary_wgs84: list[tuple[float, float]] = Field(min_length=3)
    origin_wgs84: tuple[float, float, float]
    ground_elevation_m: ScalarValue
    world_dimensions_m: Vector3Value
    launch_zone_width_m: ScalarValue
    launch_zone_length_m: ScalarValue
    terrain_mode: Literal["FLAT"] = "FLAT"
    lighting: str = "DAY"
    environment_profile_id: str


class SurvivorPlacement(BaseModel):
    survivor_id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    up_m: float = 0.0
    yaw_deg: float = 0.0
    body_state: Literal["standing", "sitting", "lying"] = "standing"
    visual_variant: str = "PROCEDURAL_ORANGE"
    occlusion_profile: str = "NONE"
    enabled: bool = True


class SurvivorScenario(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: str
    survivors: list[SurvivorPlacement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_survivors(self) -> "SurvivorScenario":
        ids = [item.survivor_id for item in self.survivors]
        if len(set(ids)) != len(ids):
            raise ValueError("survivor IDs must be unique")
        return self


class SimulationConfig(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    config_id: str
    drone_profile_id: str
    camera_profile_id: str
    environment_profile_id: str
    mission_world_profile_id: str
    survivor_scenario_id: str
    random_seed: int
    execution_source: Literal["SITL_GAZEBO"] = "SITL_GAZEBO"

    def fingerprint(self, *profiles: BaseModel) -> str:
        payload = {
            "config": self.model_dump(mode="json"),
            "profiles": [profile.model_dump(mode="json") for profile in profiles],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _load(path: str | Path, model: type[BaseModel]) -> BaseModel:
    return model.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_drone_profile(path: str | Path) -> DroneProfile:
    return _load(path, DroneProfile)  # type: ignore[return-value]


def load_camera_profile(path: str | Path) -> CameraProfile:
    return _load(path, CameraProfile)  # type: ignore[return-value]


def load_environment_profile(path: str | Path) -> EnvironmentProfile:
    return _load(path, EnvironmentProfile)  # type: ignore[return-value]
