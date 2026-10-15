"""Close-range reacquisition contracts and deterministic synthetic provider."""

from __future__ import annotations

import math
import random
import time
from abc import ABC, abstractmethod

from pydantic import BaseModel
from shapely.geometry import Point, Polygon

from .models import ReacquisitionObservation, SyntheticSurvivorScenario


class ReacquisitionProvider(ABC):
    @abstractmethod
    def observe(self, vehicle_east_m: float, vehicle_north_m: float) -> ReacquisitionObservation: ...


class SyntheticReacquisitionProvider(ReacquisitionProvider):
    """Test-only visibility based on true target minus actual SITL position."""

    def __init__(self, scenario: SyntheticSurvivorScenario, visibility_radius_m: float, noise_stddev_m: float = 0):
        self.scenario = scenario
        self.visibility_radius_m = visibility_radius_m
        self.noise_stddev_m = noise_stddev_m
        self.random = random.Random(scenario.seed)

    def observe(self, vehicle_east_m: float, vehicle_north_m: float) -> ReacquisitionObservation:
        east = self.scenario.true_east_m - vehicle_east_m
        north = self.scenario.true_north_m - vehicle_north_m
        visible = math.hypot(east, north) <= self.visibility_radius_m
        if visible and self.noise_stddev_m:
            east += self.random.gauss(0, self.noise_stddev_m)
            north += self.random.gauss(0, self.noise_stddev_m)
        return ReacquisitionObservation(
            timestamp_monotonic_ns=time.monotonic_ns(), target_id=self.scenario.target_id,
            visible=visible, relative_east_m=east if visible else None,
            relative_north_m=north if visible else None,
            confidence=0.95 if visible else 0, quality="EXACT" if not self.noise_stddev_m else "NOISY",
        )


class ReacquisitionSearchPoint(BaseModel):
    east_m: float
    north_m: float


class ReacquisitionSearchPlanner:
    """Small bounded cross pattern, clipped by the operational region."""

    def __init__(self, region: Polygon, radius_m: float):
        if radius_m <= 0:
            raise ValueError("Reacquisition search radius must be positive")
        self.region, self.radius = region, radius_m

    def plan(self, center_east_m: float, center_north_m: float) -> list[ReacquisitionSearchPoint]:
        offsets = [(0, 0), (self.radius, 0), (0, self.radius), (-self.radius, 0), (0, -self.radius), (0, 0)]
        points = [
            ReacquisitionSearchPoint(east_m=center_east_m + east, north_m=center_north_m + north)
            for east, north in offsets
            if self.region.buffer(1e-6).covers(Point(center_east_m + east, center_north_m + north))
        ]
        if not points:
            raise ValueError("No bounded reacquisition search point lies inside operational region")
        return points
