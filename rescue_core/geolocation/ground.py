"""Ground surface abstraction and guarded ENU ray intersection."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from .transforms import normalize_vector
from ..terrain.provider import TerrainProvider


class GroundModel(ABC):
    source: str

    @abstractmethod
    def elevation_up_m(self, east_m: float, north_m: float) -> float:
        """Return mission-ENU ground elevation."""


class FlatGroundModel(GroundModel):
    def __init__(self, ground_up_m: float = 0, source: str = "mission_origin"):
        self.ground_up_m = float(ground_up_m)
        self.source = source

    def elevation_up_m(self, east_m: float, north_m: float) -> float:
        return self.ground_up_m


class KnownElevationGroundModel(FlatGroundModel):
    """A known constant elevation, kept distinct for provenance."""


class TerrainGroundModel(GroundModel):
    def __init__(self, provider: TerrainProvider):
        self.provider = provider
        self.source = provider.source

    def elevation_up_m(self, east_m: float, north_m: float) -> float:
        elevation = self.provider.get_elevation_enu(east_m, north_m)
        if elevation is None:
            raise ValueError("TERRAIN_UNAVAILABLE: elevation data is unavailable for these coordinates")
        return elevation


@dataclass(frozen=True)
class GroundIntersection:
    point_enu_m: np.ndarray
    distance_m: float


class RayGroundIntersector:
    def __init__(self, parallel_epsilon: float = 1e-6, maximum_range_m: float = 10_000, max_iterations: int = 10):
        self.parallel_epsilon = parallel_epsilon
        self.maximum_range_m = maximum_range_m
        self.max_iterations = max_iterations

    def intersect(self, origin_enu_m: np.ndarray, ray_enu: np.ndarray, ground: GroundModel) -> GroundIntersection:
        origin = np.asarray(origin_enu_m, dtype=float)
        ray = normalize_vector(np.asarray(ray_enu, dtype=float))
        if not np.isfinite(origin).all():
            raise ValueError("GROUND_INTERSECTION_INVALID: camera origin is invalid")
        if ray[2] >= -self.parallel_epsilon:
            raise ValueError("GROUND_INTERSECTION_INVALID: ray points upward or is parallel to ground")

        if isinstance(ground, FlatGroundModel):
            # Fast analytical intersection for flat ground
            target_up = ground.elevation_up_m(float(origin[0]), float(origin[1]))
            distance = (target_up - origin[2]) / ray[2]
            if distance <= 0 or distance > self.maximum_range_m:
                raise ValueError("GROUND_INTERSECTION_INVALID: intersection is behind camera or beyond range limit")
            point = origin + distance * ray
            return GroundIntersection(point_enu_m=point, distance_m=float(distance))
            
        # Ray-marching for variable terrain (Newton-Raphson approximation)
        # Start with an initial guess assuming flat ground at the origin's elevation projection
        target_up = ground.elevation_up_m(float(origin[0]), float(origin[1]))
        distance = (target_up - origin[2]) / ray[2]
        
        for _ in range(self.max_iterations):
            if distance <= 0 or distance > self.maximum_range_m:
                raise ValueError("GROUND_INTERSECTION_INVALID: intersection is behind camera or beyond range limit")
            point = origin + distance * ray
            # Evaluate terrain at the current ray XY position
            terrain_z = ground.elevation_up_m(float(point[0]), float(point[1]))
            error = terrain_z - point[2]
            
            # If the error is very small, we've found the intersection
            if abs(error) < 0.01:
                point[2] = terrain_z
                return GroundIntersection(point_enu_m=point, distance_m=float(distance))
                
            # Adjust distance based on error. Since ray[2] is negative, to increase point[2] we decrease distance.
            distance += error / ray[2]
            
        raise ValueError("GROUND_INTERSECTION_INVALID: ray intersection failed to converge on variable terrain")


