"""Normalized local vision event envelopes; no geographic fields are allowed."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class VisionEvent:
    type: Literal["vision.frame_stats", "vision.detection", "vision.health"]
    timestamp_monotonic_ns: int
    timestamp_wall_ns: int
    source_id: str
    payload: dict[str, Any]

    @classmethod
    def create(cls, event_type: str, source_id: str, payload: dict[str, Any]) -> "VisionEvent":
        return cls(event_type, time.monotonic_ns(), time.time_ns(), source_id, payload)  # type: ignore[arg-type]

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp_monotonic_ns": self.timestamp_monotonic_ns,
            "timestamp_wall_ns": self.timestamp_wall_ns,
            "source_id": self.source_id,
            "payload": self.payload,
        }
