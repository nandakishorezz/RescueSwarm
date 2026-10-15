"""Geometric fleet coverage union; diversion and transit trails never count."""

from __future__ import annotations

from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import unary_union


class FleetProgressTracker:
    def __init__(self, safe_region: Polygon, effective_swath_m: float):
        if not safe_region.is_valid or safe_region.is_empty:
            raise ValueError("Fleet progress requires a valid non-empty safe region")
        if effective_swath_m <= 0:
            raise ValueError("effective_swath_m must be positive")
        self.safe_region = safe_region
        self.effective_swath_m = effective_swath_m
        self._search_trails: dict[str, list[tuple[float, float]]] = {}
        self._all_trails: dict[str, list[tuple[float, float, str]]] = {}

    def add(self, vehicle_id: str, east_m: float, north_m: float, phase: str) -> None:
        self._all_trails.setdefault(vehicle_id, []).append((east_m, north_m, phase))
        if phase == "SEARCHING":
            self._search_trails.setdefault(vehicle_id, []).append((east_m, north_m))

    def _geometry(self, vehicle_id: str):
        trail = self._search_trails.get(vehicle_id, [])
        if len(trail) < 2:
            return Polygon()
        return LineString(trail).buffer(self.effective_swath_m / 2, cap_style="square").intersection(self.safe_region)

    def per_vehicle_coverage_percent(self) -> dict[str, float]:
        return {
            vehicle_id: min(100, self._geometry(vehicle_id).area / self.safe_region.area * 100)
            for vehicle_id in sorted(self._all_trails)
        }

    def combined_geometry(self):
        geometries = [self._geometry(vehicle_id) for vehicle_id in self._all_trails]
        return unary_union(geometries).intersection(self.safe_region) if geometries else Polygon()

    def combined_coverage_percent(self) -> float:
        return min(100, self.combined_geometry().area / self.safe_region.area * 100)

    def geojson(self) -> dict[str, object]:
        return {"type": "Feature", "properties": {"coverage_percent": self.combined_coverage_percent()}, "geometry": mapping(self.combined_geometry())}

    def snapshot(self) -> dict[str, object]:
        return {
            "per_vehicle_coverage_percent": self.per_vehicle_coverage_percent(),
            "combined_coverage_percent": self.combined_coverage_percent(),
            "search_trail_points": {key: len(value) for key, value in self._search_trails.items()},
        }
