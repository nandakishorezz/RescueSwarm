"""Normalized vehicle-source boundary for simulation, replay, and future real telemetry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .drone_simulator import SimulatorUpdate


class VehicleSource(ABC):
    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def next_update(self) -> "SimulatorUpdate | None": ...
