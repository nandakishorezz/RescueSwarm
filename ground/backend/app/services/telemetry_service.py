"""Realtime message envelope and stale-link classification."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from ..domain.vehicle import LinkState


class RealtimeEnvelope(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mission_id: str
    vehicle_id: str | None
    sequence: int = Field(ge=0)
    payload: dict[str, Any]


def classify_link_age(
    age_seconds: float,
    connected_threshold_seconds: float = 2.0,
    degraded_threshold_seconds: float = 5.0,
) -> LinkState:
    if age_seconds < 0:
        return LinkState.CONNECTED
    if age_seconds < connected_threshold_seconds:
        return LinkState.CONNECTED
    if age_seconds <= degraded_threshold_seconds:
        return LinkState.DEGRADED
    return LinkState.STALE

