"""Compact normalized events for local IPC/GCS transport; no matrices are broadcast."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import FusedTargetEstimate, GeolocationHealth, TargetObservation


class GeolocationEvent(BaseModel):
    type: Literal[
        "target.observation", "target.candidate", "target.confirmed", "target.updated",
        "target.rejected", "geolocation.health", "clock_sync.health",
    ]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mission_id: str | None = None
    vehicle_id: str | None = None
    target_id: str | None = None
    payload: dict[str, Any]


def observation_event(mission_id: str, observation: TargetObservation) -> GeolocationEvent:
    return GeolocationEvent(
        type="target.observation", mission_id=mission_id, vehicle_id=observation.vehicle_id,
        target_id=observation.target_track_id,
        payload={
            "observation_id": observation.observation_id, "frame_id": observation.frame_id,
            "chosen_pixel": observation.chosen_pixel, "pixel_strategy": observation.pixel_strategy,
            "east_m": observation.target_east_m, "north_m": observation.target_north_m,
            "latitude": observation.target_latitude, "longitude": observation.target_longitude,
            "uncertainty_m": observation.position_uncertainty_m, "quality": observation.quality,
            "pose_age_ms": observation.interpolated_pose.diagnostics.nearest_sample_age_ms,
            "clock_sync_quality": observation.interpolated_pose.diagnostics.clock_sync_quality,
        },
    )


def target_event(target: FusedTargetEstimate) -> GeolocationEvent:
    event_type = "target.confirmed" if target.state.value == "CONFIRMED" else "target.candidate"
    return GeolocationEvent(
        type=event_type, mission_id=target.mission_id, vehicle_id=target.assigned_vehicle_id,
        target_id=target.target_id, payload={
            "target_id": target.target_id, "mission_id": target.mission_id,
            "state": target.state, "latitude": target.latitude, "longitude": target.longitude,
            "east_m": target.east_m, "north_m": target.north_m,
            "detection_confidence": target.detection_confidence,
            "geolocation_uncertainty_m": target.uncertainty_m,
            "observation_count": target.observation_count,
            "first_seen_at": target.first_seen_at.isoformat(), "last_seen_at": target.last_seen_at.isoformat(),
            "delivery_state": target.delivery_state,
            "assigned_vehicle_id": target.assigned_vehicle_id,
            "evidence_thumbnail": target.evidence_references[0] if target.evidence_references else None,
            "simulated": target.simulated, "geolocation_quality": target.quality,
            "inlier_count": target.inlier_count, "outlier_count": target.outlier_count,
            "evidence_references": target.evidence_references,
        },
    )


def health_event(health: GeolocationHealth) -> GeolocationEvent:
    return GeolocationEvent(type="geolocation.health", payload=health.model_dump(mode="json"))
