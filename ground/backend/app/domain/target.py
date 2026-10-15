"""Survivor/target state models; Stage 2 values originate only from simulation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class TargetState(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    ASSIGNED = "ASSIGNED"
    APPROACHING = "APPROACHING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    REJECTED = "REJECTED"


class DeliveryState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    DELIVERY_QUEUED = "DELIVERY_QUEUED"
    APPROACHING = "APPROACHING"
    IN_PROGRESS = "IN_PROGRESS"
    RELEASED = "RELEASED"
    DELIVERED = "DELIVERED"
    DELIVERED_VERIFIED = "DELIVERED_VERIFIED"
    DEFERRED = "DEFERRED"
    FAILED = "FAILED"


class Target(BaseModel):
    target_id: str = Field(default_factory=lambda: str(uuid4()))
    mission_id: str
    state: TargetState = TargetState.CANDIDATE
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    east_m: float
    north_m: float
    detection_confidence: float = Field(ge=0, le=1)
    geolocation_uncertainty_m: float = Field(ge=0)
    observation_count: int = Field(default=1, ge=1)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delivery_state: DeliveryState = DeliveryState.PENDING
    assigned_vehicle_id: str | None = None
    evidence_thumbnail: str | None = None
    simulated: bool = True
    geolocation_quality: str | None = None
    inlier_count: int | None = Field(default=None, ge=0)
    outlier_count: int | None = Field(default=None, ge=0)
    evidence_references: list[str] = Field(default_factory=list)
    delivery_point_east_m: float | None = None
    delivery_point_north_m: float | None = None
