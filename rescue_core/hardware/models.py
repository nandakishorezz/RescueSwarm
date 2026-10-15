"""Evidence-first models for physical bench validation.

The models deliberately distinguish presence/configuration from physical verification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from rescue_core.geolocation.models import GroundTruthSource, GroundTruthTarget


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_TESTED = "NOT_TESTED"
    BLOCKED = "BLOCKED"


class HardwareEvidence(BaseModel):
    component: str
    status: EvidenceStatus
    reason: str
    measured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_files: list[str] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)


GroundTruthMethod = GroundTruthSource


class PowerHealth(BaseModel):
    raw_value: int | None = None
    source: str = "vcgencmd get_throttled"
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    undervoltage_now: bool | None = None
    frequency_capped_now: bool | None = None
    throttled_now: bool | None = None
    soft_temperature_limit_now: bool | None = None
    undervoltage_occurred: bool | None = None
    frequency_capping_occurred: bool | None = None
    throttling_occurred: bool | None = None
    soft_temperature_limit_occurred: bool | None = None
    valid_for_precision_measurement: bool = False
    reasons: list[str] = Field(default_factory=list)
