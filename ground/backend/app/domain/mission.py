"""Mission and canonical WGS84 boundary models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from .flight_parameters import FlightParameters


class MissionState(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    READY = "READY"
    RUNNING = "RUNNING"
    RETURNING = "RETURNING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = None


class Boundary(BaseModel):
    """One exterior polygon ring in EPSG:4326; closure is implicit."""

    vertices: list[GeoPoint]
    source_format: str
    area_m2: float = Field(gt=0)
    area_hectares: float = Field(gt=0)
    origin: GeoPoint
    warnings: list[str] = Field(default_factory=list)


class Mission(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=120)
    state: MissionState = MissionState.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    boundary: Boundary | None = None
    area_m2: float | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    coverage_percent: float = Field(default=0, ge=0, le=100)
    flight_parameters: FlightParameters = Field(default_factory=FlightParameters)

