"""Actual flown-coverage and planned-route deviation metrics."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from ..domain.mission import GeoPoint
from ..planning.coverage_planner import sensor_footprint_swath
from ..planning.models import CoveragePlan
from ..services.coordinates import LocalCoordinateFrame


class ActualCoverageTracker:
    """Accumulate only SEARCHING trail segments; takeoff and RTL are excluded."""

    def __init__(self, plan: CoveragePlan, origin: GeoPoint):
        self.plan = plan
        self.frame = LocalCoordinateFrame(origin)
        self.safe_polygon = Polygon([(p.east_m, p.north_m) for p in plan.safe_polygon_enu])
        self.planned_line = LineString([(p.east_m, p.north_m) for p in plan.path_points_enu])
        self.trail: list[GeoPoint] = []
        self.search_points: list[tuple[float, float]] = []
        self._swath_pieces: list[object] = []

    def add(self, point: GeoPoint, searching: bool) -> None:
        if self.trail and self._same_point(self.trail[-1], point):
            return
        self.trail.append(point)
        if not searching:
            return
        local = self.frame.to_enu(point)
        current = (local.east_m, local.north_m)
        if self.search_points and Point(self.search_points[-1]).distance(Point(current)) > 0.01:
            segment = LineString([self.search_points[-1], current])
            self._swath_pieces.append(sensor_footprint_swath(
                segment,
                self.plan.configuration.effective_swath_m,
                self.plan.configuration.along_track_footprint_m,
            ).intersection(self.safe_polygon))
        self.search_points.append(current)

    def metrics(self) -> dict[str, float]:
        union = unary_union(self._swath_pieces) if self._swath_pieces else Polygon()
        covered = union.area
        flown_search_length = sum(
            Point(left).distance(Point(right))
            for left, right in zip(self.search_points, self.search_points[1:])
        )
        gross_swath_area = flown_search_length * self.plan.configuration.effective_swath_m
        actual_path = self._path_length(self.trail)
        deviations = [self.planned_line.distance(Point(point)) for point in self.search_points]
        ordered = sorted(deviations)
        p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return {
            "actual_path_length_m": actual_path,
            "actual_coverage_percent": min(100, covered / self.safe_polygon.area * 100),
            "missed_area_m2": max(0, self.safe_polygon.area - covered),
            # Approximation excludes artificial overlap from the round caps on
            # every high-rate sample while retaining revisited swath area.
            "overlap_area_m2": max(0, gross_swath_area - covered),
            "mean_deviation_m": sum(deviations) / len(deviations) if deviations else 0,
            "p95_deviation_m": ordered[p95_index] if ordered else 0,
            "max_deviation_m": max(deviations, default=0),
        }

    def _path_length(self, points: list[GeoPoint]) -> float:
        local = [self.frame.to_enu(point) for point in points]
        return sum(
            math.hypot(right.east_m - left.east_m, right.north_m - left.north_m)
            for left, right in zip(local, local[1:])
        )

    @staticmethod
    def _same_point(left: GeoPoint, right: GeoPoint) -> bool:
        return math.isclose(left.latitude, right.latitude, abs_tol=1e-9) and math.isclose(
            left.longitude, right.longitude, abs_tol=1e-9
        )
