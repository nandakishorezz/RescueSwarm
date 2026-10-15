"""Disposable-SITL-only parameter evidence for native failsafe scenarios."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from .guard import SITLSafetyGuard


class ParameterAdapter(Protocol):
    connection_string: str

    def parameter_value(self, name: str) -> float | None: ...
    def set_parameter(self, name: str, value: float) -> None: ...


class SITLParameterEvidence(BaseModel):
    name: str
    original: float
    scenario_value: float
    restored: bool = False


class SITLScenarioParameterSet:
    """Records/restores changes and fails closed before any non-loopback adapter call."""

    def __init__(self, adapter: ParameterAdapter):
        SITLSafetyGuard("sitl", "simulation_only").validate(adapter.connection_string)
        self.adapter = adapter
        self.changes: list[SITLParameterEvidence] = []

    def apply(self, name: str, value: float) -> SITLParameterEvidence:
        original = self.adapter.parameter_value(name)
        if original is None:
            raise RuntimeError(f"SITL parameter unavailable: {name}")
        self.adapter.set_parameter(name, value)
        evidence = SITLParameterEvidence(name=name, original=original, scenario_value=value)
        self.changes.append(evidence)
        return evidence

    def restore(self) -> None:
        for evidence in reversed(self.changes):
            self.adapter.set_parameter(evidence.name, evidence.original)
            evidence.restored = True
