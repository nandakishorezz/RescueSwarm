"""Opt-in simulation-only fleet session. Single-drone mission ownership stays separate."""

from .contracts import (
    AssignmentState,
    DroneInstance,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    ServiceAssignment,
    SharedSurvivorRecord,
    SurvivorObservation,
    default_configuration,
)
from .coordinator import ExclusiveServiceCoordinator
from .registry import SharedSurvivorRegistry
from .runtime import MultiDroneRuntime

__all__ = [
    "AssignmentState",
    "DroneInstance",
    "DroneRole",
    "ExclusiveServiceCoordinator",
    "FleetConfiguration",
    "FleetSession",
    "MultiDroneRuntime",
    "ServiceAssignment",
    "SharedSurvivorRecord",
    "SharedSurvivorRegistry",
    "SurvivorObservation",
    "default_configuration",
]
