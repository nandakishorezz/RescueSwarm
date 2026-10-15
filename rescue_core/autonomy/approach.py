"""Operational-region inset and safe internal GUIDED approach paths."""

from __future__ import annotations

import heapq
import math

from pydantic import BaseModel, Field
from shapely.geometry import LineString, Point, Polygon


class ApproachPlanningError(ValueError):
    pass


class ApproachPoint(BaseModel):
    east_m: float
    north_m: float
    altitude_m: float = Field(gt=0)


class GuidedApproachPlan(BaseModel):
    points: list[ApproachPoint]
    path_length_m: float = Field(ge=0)
    direct_path_used: bool


def operational_flight_region(boundary_enu: Polygon, safety_margin_m: float) -> Polygon:
    if safety_margin_m < 0:
        raise ValueError("Operational safety margin cannot be negative")
    safe = boundary_enu.buffer(-safety_margin_m, join_style="mitre")
    if safe.is_empty or safe.geom_type != "Polygon" or not safe.is_valid:
        raise ApproachPlanningError("Operational safety margin collapses or fragments the flight region")
    return safe


class GuidedApproachPlanner:
    def __init__(self, region: Polygon, tolerance_m: float = 5.0):
        if region.is_empty or not region.is_valid or region.geom_type != "Polygon":
            raise ValueError("GUIDED operational region must be one valid Polygon")
        self.region = region
        self.allowed = region.buffer(tolerance_m)

    def plan(
        self, current_east_m: float, current_north_m: float,
        target_east_m: float, target_north_m: float, altitude_m: float,
    ) -> GuidedApproachPlan:
        start, end = (current_east_m, current_north_m), (target_east_m, target_north_m)
        if not self.allowed.covers(Point(start)):
            nearest = self.region.exterior.interpolate(self.region.exterior.project(Point(start)))
            if Point(start).distance(nearest) <= 15.0:
                start = (nearest.x, nearest.y)
            else:
                raise ApproachPlanningError("Vehicle is outside operational flight region")
        if not self.allowed.covers(Point(end)):
            nearest_end = self.region.exterior.interpolate(self.region.exterior.project(Point(end)))
            if Point(end).distance(nearest_end) <= 15.0:
                end = (nearest_end.x, nearest_end.y)
            else:
                raise ApproachPlanningError("Target is outside operational flight region")
        coordinates = self._connector(start, end)
        points = [ApproachPoint(east_m=e, north_m=n, altitude_m=altitude_m) for e, n in coordinates[1:]]
        length = sum(math.dist(left, right) for left, right in zip(coordinates, coordinates[1:]))
        return GuidedApproachPlan(points=points, path_length_m=length, direct_path_used=len(coordinates) == 2)

    def _connector(self, start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
        if self.allowed.covers(LineString([start, end])):
            return [start, end]
        vertices = [start, end, *list(self.region.exterior.coords)[:-1]]
        adjacency: dict[int, list[tuple[float, int]]] = {i: [] for i in range(len(vertices))}
        for left in range(len(vertices)):
            for right in range(left + 1, len(vertices)):
                line = LineString([vertices[left], vertices[right]])
                if self.allowed.covers(line):
                    adjacency[left].append((line.length, right))
                    adjacency[right].append((line.length, left))
        distances, previous = [math.inf] * len(vertices), [None] * len(vertices)
        distances[0] = 0
        queue = [(0.0, 0)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == 1:
                break
            for length, neighbor in adjacency[node]:
                candidate = distance + length
                if candidate < distances[neighbor]:
                    distances[neighbor], previous[neighbor] = candidate, node
                    heapq.heappush(queue, (candidate, neighbor))
        if not math.isfinite(distances[1]):
            raise ApproachPlanningError("No internal route to target")
        route = [1]
        while route[-1] != 0:
            parent = previous[route[-1]]
            if parent is None:
                raise ApproachPlanningError("Internal route reconstruction failed")
            route.append(parent)
        coordinates = [vertices[index] for index in reversed(route)]
        if any(not self.allowed.covers(LineString(pair)) for pair in zip(coordinates, coordinates[1:])):
            raise ApproachPlanningError("Generated approach route exits operational region")
        return coordinates
