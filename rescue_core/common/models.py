"""Serializable diagnostic result models with explicit evidence states."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceStatus(StrEnum):
    """Permitted outcome states for diagnostics and generated reports."""

    CONFIGURED = "CONFIGURED"
    DETECTED = "DETECTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_TESTED = "NOT_TESTED"


class DiagnosticResult(BaseModel):
    """One diagnostic result; values must be backed by collected evidence."""

    name: str
    status: EvidenceStatus
    reason: str | None = None
    value: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def json_safe(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return self.model_dump(mode="json")

