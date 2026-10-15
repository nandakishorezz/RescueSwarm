"""Autopilot-independent coverage planning."""

from .coverage_planner import CoveragePlanner, PlanningError
from .models import CoveragePlan, CoverageRequest, PathKind, PlanValidation, SweepStrategy

__all__ = [
    "CoveragePlan", "CoveragePlanner", "CoverageRequest", "PathKind", "PlanningError",
    "PlanValidation", "SweepStrategy",
]

