"""Camera and detector liveness state without frozen-frame ambiguity."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class HealthState(StrEnum):
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(slots=True)
class ComponentHealth:
    state: HealthState = HealthState.INITIALIZING
    last_success_monotonic_ns: int | None = None
    errors: int = 0
    reason: str | None = None

    def success(self, timestamp_ns: int | None = None) -> None:
        self.last_success_monotonic_ns = timestamp_ns or time.monotonic_ns()
        self.state = HealthState.READY
        self.reason = None

    def error(self, reason: str, degraded_until: int = 3) -> None:
        self.errors += 1
        self.reason = reason
        self.state = HealthState.DEGRADED if self.errors < degraded_until else HealthState.FAILED

    def evaluate_timeout(self, timeout_s: float, now_ns: int | None = None) -> HealthState:
        now = now_ns or time.monotonic_ns()
        if self.last_success_monotonic_ns is None:
            return self.state
        if (now - self.last_success_monotonic_ns) / 1e9 > timeout_s:
            self.state = HealthState.FAILED
            self.reason = f"No successful update for more than {timeout_s:g}s"
        return self.state

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "last_success_monotonic_ns": self.last_success_monotonic_ns,
            "errors": self.errors,
            "reason": self.reason,
        }
