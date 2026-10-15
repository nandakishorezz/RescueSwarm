"""Ground server configuration with environment-safe database override."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ApplicationConfig(BaseModel):
    mode: Literal["development", "engineering", "setup", "mission", "review", "competition"]
    name: str


class CompetitionConfig(BaseModel):
    enabled: bool
    external_network_allowed: bool
    max_area_hectares: float = Field(gt=0)
    minimum_deployed_drones: int = Field(default=2, ge=2)
    maximum_survivors: int = Field(default=10, ge=1)
    maximum_mission_duration_s: float = Field(default=1800, gt=0)
    maximum_setup_duration_s: float = Field(default=300, gt=0)
    maximum_combined_mass_kg: float = Field(default=25, gt=0)
    payload_mass_g: float = Field(default=200, gt=0)
    payload_dimensions_cm: tuple[float, float, float] = (20, 10, 5)


class ServerConfig(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    allowed_origins: list[str]

    @model_validator(mode="after")
    def reject_wildcard(self) -> "ServerConfig":
        if "*" in self.allowed_origins:
            raise ValueError("Wildcard CORS origins are not allowed")
        return self


class DatabaseConfig(BaseModel):
    url: str
    telemetry_persist_interval_seconds: float = Field(gt=0)


class NetworkConfig(BaseModel):
    external_internet_required: bool
    ground_ip: str
    subnet: str
    ssid: str
    mdns_name: str


class MapConfig(BaseModel):
    style: Literal["local_blank"]
    external_tile_sources: list[str]


class RealtimeConfig(BaseModel):
    connected_threshold_seconds: float = Field(gt=0)
    degraded_threshold_seconds: float = Field(gt=0)
    trail_max_points: int = Field(gt=0, le=10_000)
    recent_events_max: int = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> "RealtimeConfig":
        if self.degraded_threshold_seconds <= self.connected_threshold_seconds:
            raise ValueError("degraded threshold must exceed connected threshold")
        return self


class UploadConfig(BaseModel):
    max_bytes: int = Field(gt=0, le=10 * 1024 * 1024)
    allowed_extensions: list[str]


class SimulatorConfig(BaseModel):
    enabled: bool
    seed: int
    update_interval_seconds: float = Field(gt=0)
    total_steps: int = Field(ge=8, le=10_000)


class CoverageConfig(BaseModel):
    search_altitude_m: float = Field(gt=0)
    lane_spacing_m: float = Field(gt=0)
    effective_swath_m: float = Field(gt=0)
    edge_margin_m: float = Field(ge=0)
    search_speed_m_s: float = Field(gt=0)
    sweep_strategy: Literal["auto_optimized", "fixed_angle"]
    fixed_sweep_angle_deg: float = Field(ge=0, lt=180)
    turn_penalty_m: float = Field(ge=0)
    min_planned_coverage_percent: float = Field(gt=0, le=100)
    max_waypoints: int = Field(ge=3, le=5_000)


class SitlConfig(BaseModel):
    enabled: bool
    environment: Literal["sitl"]
    command_authority: Literal["simulation_only"]
    vehicle: Literal["ArduCopter"]
    frame: str
    connection: str
    readiness_timeout_s: float = Field(gt=0)
    heartbeat_timeout_s: float = Field(gt=0)
    command_timeout_s: float = Field(gt=0)
    mission_timeout_s: float = Field(gt=0)
    rtl_timeout_s: float = Field(gt=0)
    launcher_speedup: int = Field(ge=1, le=100)
    ardupilot_home: str | None = None
    wsl_distribution: str = "Ubuntu"
    message_rates_hz: dict[str, float]


class FleetConfig(BaseModel):
    enabled: bool = True
    fleet_size: int = Field(default=2, ge=2, le=10)
    allow_degraded_start: bool = False
    base_port: int = Field(default=5760, ge=1, le=65535)
    spawn_offset_bearing_deg: float = Field(default=90, ge=0, lt=360)
    spawn_offset_distance_m: float = Field(default=2, gt=0)
    payload_slots_per_vehicle: int = Field(default=2, ge=0, le=10)
    launch_zone_width_m: float = Field(default=3.6576, gt=0)
    launch_zone_length_m: float = Field(default=3.6576, gt=0)
    departure_clear_altitude_m: float = Field(default=3, gt=0)
    separation_warning_horizontal_m: float = Field(default=8, gt=0)
    separation_critical_horizontal_m: float = Field(default=3, gt=0)
    separation_vertical_clear_m: float = Field(default=4, gt=0)
    target_association_radius_m: float = Field(default=6, gt=0)
    maximum_target_uncertainty_m: float = Field(default=15, gt=0)
    minimum_delivery_battery_percent: float = Field(default=25, ge=0, le=100)

    @model_validator(mode="after")
    def validate_fleet_defaults(self) -> "FleetConfig":
        if self.separation_critical_horizontal_m >= self.separation_warning_horizontal_m:
            raise ValueError("Fleet critical separation must be below warning separation")
        return self


class ExternalServicesConfig(BaseModel):
    api_dependencies: list[str]
    auth_dependencies: list[str]


class VehicleGeometryConfig(BaseModel):
    profile_id: str = "SITL_REFERENCE_PROFILE"
    length_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    arm_span_m: float | None = Field(default=None, gt=0)
    rotor_radius_m: float | None = Field(default=None, gt=0)
    body_origin_offset_m: tuple[float, float, float] = (0, 0, 0)
    autopilot_reference_offset_m: tuple[float, float, float] = (0, 0, 0)
    camera_mount_position_m: tuple[float, float, float] = (0, 0, 0)
    payload_mount_position_m: tuple[float, float, float] = (0, 0, 0)
    evidence_class: Literal["CONFIGURED_ASSUMPTION", "PHYSICAL_MEASURED", "PHYSICAL_UNVERIFIED"] = "CONFIGURED_ASSUMPTION"


class DigitalTwinCameraConfig(BaseModel):
    calibration_id: str = "stage7s_synthetic_pinhole"
    extrinsics_id: str = "stage7s_synthetic_nadir"
    sensor_mode: str = "SYNTHETIC_1280x800"
    mount_type: Literal["FIXED", "GIMBAL"] = "FIXED"
    evidence_class: Literal["SYNTHETIC", "PHYSICAL_MEASURED", "PHYSICAL_UNVERIFIED", "CONFIGURED_ASSUMPTION"] = "SYNTHETIC"


class DigitalTwinRenderingConfig(BaseModel):
    vehicle_scale_mode: Literal["icon", "true_scale"] = "icon"
    show_camera_frustum: bool = True
    show_camera_footprint: bool = True
    trail_max_points: int = Field(default=1_000, gt=0, le=10_000)
    replay_stale_gap_ms: int = Field(default=2_000, gt=0)


class DigitalTwinGroundConfig(BaseModel):
    provider: Literal["none", "flat", "local_dem"] = "flat"
    elevation_up_m: float = 0
    evidence_class: Literal["CONFIGURED_ASSUMPTION", "PHYSICAL_MEASURED", "UNKNOWN"] = "CONFIGURED_ASSUMPTION"


class DigitalTwinConfig(BaseModel):
    vehicle_geometry: VehicleGeometryConfig = Field(default_factory=VehicleGeometryConfig)
    camera: DigitalTwinCameraConfig = Field(default_factory=DigitalTwinCameraConfig)
    rendering: DigitalTwinRenderingConfig = Field(default_factory=DigitalTwinRenderingConfig)
    ground: DigitalTwinGroundConfig = Field(default_factory=DigitalTwinGroundConfig)


class GroundConfig(BaseModel):
    application: ApplicationConfig
    competition: CompetitionConfig
    server: ServerConfig
    database: DatabaseConfig
    network: NetworkConfig
    map: MapConfig
    realtime: RealtimeConfig
    uploads: UploadConfig
    simulator: SimulatorConfig
    coverage: CoverageConfig
    sitl: SitlConfig
    fleet: FleetConfig = Field(default_factory=FleetConfig)
    external_services: ExternalServicesConfig
    digital_twin: DigitalTwinConfig = Field(default_factory=DigitalTwinConfig)


def load_ground_config(path: str | Path | None = None) -> GroundConfig:
    config_path = Path(path or os.getenv("RESCUESWARM_GROUND_CONFIG", "config/ground.yaml"))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Ground configuration root must be a mapping")
    database_override = os.getenv("RESCUESWARM_DATABASE_URL")
    if database_override:
        raw.setdefault("database", {})["url"] = database_override
    return GroundConfig.model_validate(raw)
