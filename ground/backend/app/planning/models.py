"""Coverage-plan domain models independent of any flight controller."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ..domain.flight_parameters import FlightParameters
from ..domain.mission import GeoPoint


class SweepStrategy(StrEnum):
    AUTO_OPTIMIZED = "AUTO_OPTIMIZED"
    FIXED_ANGLE = "FIXED_ANGLE"


class PathKind(StrEnum):
    SEARCH = "SEARCH"
    TRANSIT = "TRANSIT"


class EnuCoordinate(BaseModel):
    east_m: float
    north_m: float


class CoverageSegment(BaseModel):
    segment_id: str
    kind: PathKind
    points_enu: list[EnuCoordinate]
    points_wgs84: list[GeoPoint] = Field(default_factory=list)
    assigned_vehicle_id: str | None = "drone_01"
    search_line_index: int | None = None


class PlanValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    planned_coverage_percent: float = Field(ge=0, le=100)
    covered_area_m2: float = Field(ge=0)
    missed_area_m2: float = Field(ge=0)


class CoverageRequest(BaseModel):
    search_altitude_m: float = Field(gt=0)
    lane_spacing_m: float = Field(gt=0)
    effective_swath_m: float = Field(gt=0)
    along_track_footprint_m: float | None = Field(default=None, gt=0)
    edge_margin_m: float = Field(ge=0)
    search_speed_m_s: float = Field(gt=0)
    sweep_strategy: SweepStrategy = SweepStrategy.AUTO_OPTIMIZED
    fixed_sweep_angle_deg: float = Field(default=0, ge=0, lt=180)
    turn_penalty_m: float = Field(default=3, ge=0)
    min_planned_coverage_percent: float = Field(default=98, gt=0, le=100)
    max_waypoints: int = Field(default=500, ge=3, le=5_000)
    flight_parameters: FlightParameters | None = None


class CoveragePlan(BaseModel):
    mission_id: str
    planner_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    configuration: CoverageRequest
    safe_polygon_enu: list[EnuCoordinate]
    safe_polygon_wgs84: list[GeoPoint]
    sweep_angle_deg: float
    segments: list[CoverageSegment]
    path_points_enu: list[EnuCoordinate]
    path_points_wgs84: list[GeoPoint]
    estimated_path_length_m: float = Field(gt=0)
    estimated_search_time_s: float = Field(gt=0)
    number_of_search_lines: int = Field(gt=0)
    number_of_turns: int = Field(ge=0)
    cost_m: float = Field(gt=0)
    validation: PlanValidation
    plan_fingerprint: str | None = None
    mission_fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
