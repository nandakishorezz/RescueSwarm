"""Project-level hardware gates that cannot be advanced by software/SITL work."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class HardwareGateState(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class HardwareGateStatus(BaseModel):
    gate_id: str
    name: str
    state: HardwareGateState
    reason: str


H1_PHYSICAL_PROTOTYPE = HardwareGateStatus(
    gate_id="H1_PHYSICAL_PROTOTYPE",
    name="Pi5 + OV9281 + Pixhawk/M8N",
    state=HardwareGateState.PENDING,
    reason="Hardware temporarily unavailable; Stage 6 physical validation has not run",
)
