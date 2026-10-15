"""Deterministic boustrophedon coverage planning in mission-local ENU metres."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from ..domain.mission import Boundary
from ..services.coordinates import EnuPoint, LocalCoordinateFrame
from .models import (
    CoveragePlan,
    CoverageRequest,
    CoverageSegment,
    EnuCoordinate,
    PathKind,
    PlanValidation,
    SweepStrategy,
)

PLANNER_VERSION = "rescueswarm-coverage-v1"
GEOMETRY_TOLERANCE_M = 1e-5


def sensor_footprint_swath(
    line: LineString, cross_track_m: float, along_track_m: float | None = None,
) -> Polygon:
    """Return the rectangular nadir footprint swept along one straight leg.

    ``buffer(..., cap_style='square')`` assumes equal along/cross dimensions.
    The OV9281 footprint is wider along track for the selected sweep, so extend
    each endpoint by the real half-length before applying the cross-track
    buffer.  Existing callers without calibrated along-track geometry retain
    the original square-footprint behavior.
    """
    along = along_track_m or cross_track_m
    extension = max(0.0, (along - cross_track_m) / 2)
    coordinates = list(line.coords)
    if extension > 0 and len(coordinates) >= 2:
        start, next_point = coordinates[0], coordinates[1]
        before_end, end = coordinates[-2], coordinates[-1]

        def extended(point: tuple[float, float], other: tuple[float, float]) -> tuple[float, float]:
            dx, dy = point[0] - other[0], point[1] - other[1]
            length = math.hypot(dx, dy)
            if length <= GEOMETRY_TOLERANCE_M:
                return point
            return point[0] + extension * dx / length, point[1] + extension * dy / length

        coordinates[0] = extended(start, next_point)
        coordinates[-1] = extended(end, before_end)
        line = LineString(coordinates)
    return line.buffer(cross_track_m / 2, cap_style="square")


def compute_swath_geometry(
    search_altitude_m: float,
    overlap_percent: float = 50.0,
    horizontal_fov_rad: float = 1.3494818844471055,
    vertical_fov_rad: float = 0.9272952180016122,
) -> tuple[float, float, float]:
    """Calculate (effective_swath_m, lane_spacing_m, along_track_footprint_m) from altitude and overlap."""
    along_track = 2.0 * search_altitude_m * math.tan(horizontal_fov_rad / 2.0)
    cross_track = 2.0 * search_altitude_m * math.tan(vertical_fov_rad / 2.0)
    overlap_fraction = max(0.05, min(0.95, overlap_percent / 100.0))
    lane_spacing = cross_track * (1.0 - overlap_fraction)
    return cross_track, lane_spacing, along_track


class PlanningError(ValueError):
    pass


@dataclass
class _Candidate:
    angle: float
    segments: list[CoverageSegment]
    path: list[tuple[float, float]]
    search_lines: list[LineString]
    path_length: float
    cost: float


class CoveragePlanner:
    def plan(self, mission_id: str, boundary: Boundary, request: CoverageRequest) -> CoveragePlan:
        if request.flight_parameters is not None:
            swath, spacing, along = compute_swath_geometry(
                request.flight_parameters.search_altitude_m,
                request.flight_parameters.search_swath_overlap_percent,
            )
            request.search_altitude_m = request.flight_parameters.search_altitude_m
            request.search_speed_m_s = request.flight_parameters.search_speed_m_s
            request.effective_swath_m = swath
            request.lane_spacing_m = spacing
            request.along_track_footprint_m = along

        frame = LocalCoordinateFrame(boundary.origin)
        original = Polygon([
            (frame.to_enu(point).east_m, frame.to_enu(point).north_m)
            for point in boundary.vertices
        ])
        safe_geometry = original.buffer(-request.edge_margin_m, join_style="mitre")
        if safe_geometry.is_empty:
            raise PlanningError("Edge margin collapses the mission polygon")
        if isinstance(safe_geometry, MultiPolygon):
            raise PlanningError("Edge margin splits the safe search region into disconnected polygons")
        if not isinstance(safe_geometry, Polygon) or not safe_geometry.is_valid:
            raise PlanningError("Safe inset polygon is invalid")
        if safe_geometry.area <= GEOMETRY_TOLERANCE_M:
            raise PlanningError("Safe search area is too small")

        angles = (
            [request.fixed_sweep_angle_deg]
            if request.sweep_strategy is SweepStrategy.FIXED_ANGLE
            else self._candidate_angles(safe_geometry)
        )
        candidates: list[_Candidate] = []
        reasons: list[str] = []
        for angle in angles:
            try:
                generated = self._generate_candidate(safe_geometry, request, angle)
                candidate_validation = self._validate(safe_geometry, generated, request)
                if candidate_validation.valid:
                    candidates.append(generated)
                else:
                    reasons.append(f"{angle:.3f} degrees: {'; '.join(candidate_validation.errors)}")
            except PlanningError as exc:
                reasons.append(f"{angle:.3f} degrees: {exc}")
        if not candidates:
            raise PlanningError("No valid sweep orientation: " + "; ".join(reasons))
        candidate = min(candidates, key=lambda item: (round(item.cost, 6), round(item.angle, 6)))
        validation = self._validate(safe_geometry, candidate, request)
        if not validation.valid:
            raise PlanningError("Coverage plan validation failed: " + "; ".join(validation.errors))
        path_enu = [EnuCoordinate(east_m=x, north_m=y) for x, y in candidate.path]
        path_wgs84 = [frame.to_wgs84(EnuPoint(x, y, request.search_altitude_m)) for x, y in candidate.path]
        safe_coords = list(safe_geometry.exterior.coords)[:-1]
        safe_enu = [EnuCoordinate(east_m=x, north_m=y) for x, y in safe_coords]
        safe_wgs84 = [frame.to_wgs84(EnuPoint(x, y, 0)) for x, y in safe_coords]
        for segment in candidate.segments:
            segment.points_wgs84 = [
                frame.to_wgs84(EnuPoint(point.east_m, point.north_m, request.search_altitude_m))
                for point in segment.points_enu
            ]
        return CoveragePlan(
            mission_id=mission_id,
            planner_version=PLANNER_VERSION,
            configuration=request,
            safe_polygon_enu=safe_enu,
            safe_polygon_wgs84=safe_wgs84,
            sweep_angle_deg=candidate.angle,
            segments=candidate.segments,
            path_points_enu=path_enu,
            path_points_wgs84=path_wgs84,
            estimated_path_length_m=candidate.path_length,
            estimated_search_time_s=candidate.path_length / request.search_speed_m_s,
            number_of_search_lines=len(candidate.search_lines),
            number_of_turns=max(0, len(candidate.search_lines) - 1),
            cost_m=candidate.cost,
            validation=validation,
            metadata={
                "candidate_angles_deg": angles,
                "boundary_area_m2": boundary.area_m2,
                "safe_area_m2": safe_geometry.area,
            },
        )

    @staticmethod
    def _candidate_angles(polygon: Polygon) -> list[float]:
        coordinates = list(polygon.exterior.coords)
        angles = {0.0, 90.0}
        for start, end in zip(coordinates, coordinates[1:]):
            if Point(start).distance(Point(end)) > GEOMETRY_TOLERANCE_M:
                angle = round(math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180, 6)
                angles.add(0.0 if math.isclose(angle, 180.0, abs_tol=1e-6) else angle)
        rectangle = list(polygon.minimum_rotated_rectangle.exterior.coords)
        for start, end in zip(rectangle, rectangle[1:]):
            angle = round(math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180, 6)
            angles.add(0.0 if math.isclose(angle, 180.0, abs_tol=1e-6) else angle)
        return sorted(angles)

    def _generate_candidate(
        self, polygon: Polygon, request: CoverageRequest, angle: float
    ) -> _Candidate:
        rotated = affinity.rotate(polygon, -angle, origin=polygon.centroid)
        min_x, min_y, max_x, max_y = rotated.bounds
        if max_y - min_y < GEOMETRY_TOLERANCE_M:
            raise PlanningError("Sweep-normal width is zero")
        # Preserve the half-footprint interior lattice, then use overlap
        # already requested by the operator for additional edge guard passes.
        # The former
        # lane_spacing/2 origin left an oblique corner wedge uncovered when
        # lane spacing was narrower than the effective footprint (the live
        # 20 m / 25 m plan stopped at 97.78%).  Even distribution preserves
        # the configured maximum spacing while giving both edges equal,
        # sensor-footprint-aware treatment.
        normal_width = max_y - min_y
        footprint = request.effective_swath_m
        half_footprint = footprint / 2
        if normal_width <= footprint + GEOMETRY_TOLERANCE_M:
            y_values = [(min_y + max_y) / 2]
        else:
            # Lane spacing remains an operator input; validation must still
            # reject a requested spacing wider than the available footprint
            # instead of silently inserting denser passes.
            maximum_spacing = request.lane_spacing_m
            usable_width = normal_width - footprint
            interval_count = max(1, math.ceil(usable_width / maximum_spacing))
            spacing = usable_width / interval_count
            y_values = [min_y + half_footprint + index * spacing for index in range(interval_count + 1)]
            # Do not redistribute all lanes to obtain edge tolerance: the
            # first live attempt doing so removed the established interior
            # viewpoints and yielded only border-locked person candidates.
            # Add guard passes instead.  At 50% overlap their footprints
            # extend 2 m beyond each edge while every former interior pass is
            # retained unchanged.
            edge_reserve = max(0.0, footprint - maximum_spacing) / 2
            if edge_reserve > GEOMETRY_TOLERANCE_M:
                outer_inset = max(GEOMETRY_TOLERANCE_M, half_footprint - edge_reserve)
                # The first guard run measured 1.944 m p95 deviation against
                # a 2 m reserve and left a 0.518 m2 corner sliver.  A second
                # guard at half that inset retains a positive in-polygon
                # centreline while covering the measured tracking envelope.
                boundary_guard_inset = max(GEOMETRY_TOLERANCE_M, outer_inset / 2)
                y_values.extend((
                    min_y + boundary_guard_inset,
                    min_y + outer_inset,
                    max_y - outer_inset,
                    max_y - boundary_guard_inset,
                ))
                y_values = sorted({round(value, 9) for value in y_values})

        ordered_search: list[LineString] = []
        reverse = False
        for lane_index, lane_y in enumerate(y_values):
            infinite = LineString([(min_x - 10_000, lane_y), (max_x + 10_000, lane_y)])
            intersection = rotated.intersection(infinite)
            pieces = self._line_pieces(intersection)
            pieces.sort(key=lambda line: line.bounds[0], reverse=reverse)
            for piece in pieces:
                coords = list(piece.coords)
                if reverse:
                    coords.reverse()
                restored = affinity.rotate(LineString(coords), angle, origin=polygon.centroid)
                ordered_search.append(restored)
                reverse = not reverse
        if not ordered_search:
            raise PlanningError("No search lines intersect the safe polygon")

        segments: list[CoverageSegment] = []
        path: list[tuple[float, float]] = []
        for index, search_line in enumerate(ordered_search):
            start = tuple(search_line.coords[0])
            if path and Point(path[-1]).distance(Point(start)) > GEOMETRY_TOLERANCE_M:
                connector = self._safe_connector(polygon, path[-1], start)
                connector = self._remove_redundant(connector)
                segments.append(self._segment(f"transit_{index:03d}", PathKind.TRANSIT, connector))
                path.extend(connector[1:])
            coords = self._remove_redundant([tuple(point) for point in search_line.coords])
            segments.append(self._segment(f"search_{index:03d}", PathKind.SEARCH, coords, index))
            if not path:
                path.extend(coords)
            else:
                path.extend(coords[1:] if Point(path[-1]).distance(Point(coords[0])) <= GEOMETRY_TOLERANCE_M else coords)
        path = self._remove_adjacent_duplicates(path)
        search_distance = sum(line.length for line in ordered_search)
        transit_distance = sum(
            LineString([(point.east_m, point.north_m) for point in segment.points_enu]).length
            for segment in segments if segment.kind is PathKind.TRANSIT
        )
        turns = max(0, len(ordered_search) - 1)
        return _Candidate(
            angle=angle,
            segments=segments,
            path=path,
            search_lines=ordered_search,
            path_length=search_distance + transit_distance,
            cost=search_distance + transit_distance + turns * request.turn_penalty_m,
        )

    @staticmethod
    def _line_pieces(geometry: object) -> list[LineString]:
        if isinstance(geometry, LineString):
            return [geometry] if geometry.length > GEOMETRY_TOLERANCE_M else []
        if isinstance(geometry, (MultiLineString, GeometryCollection)):
            return [part for part in geometry.geoms if isinstance(part, LineString) and part.length > GEOMETRY_TOLERANCE_M]
        return []

    @staticmethod
    def _segment(
        segment_id: str,
        kind: PathKind,
        points: list[tuple[float, float]],
        search_index: int | None = None,
    ) -> CoverageSegment:
        return CoverageSegment(
            segment_id=segment_id,
            kind=kind,
            points_enu=[EnuCoordinate(east_m=x, north_m=y) for x, y in points],
            search_line_index=search_index,
        )

    def _safe_connector(
        self, polygon: Polygon, start: tuple[float, float], end: tuple[float, float]
    ) -> list[tuple[float, float]]:
        direct = LineString([start, end])
        allowed = polygon.buffer(GEOMETRY_TOLERANCE_M)
        if allowed.covers(direct):
            return [start, end]
        vertices = [start, end, *list(polygon.exterior.coords)[:-1]]
        adjacency: dict[int, list[tuple[float, int]]] = {index: [] for index in range(len(vertices))}
        for left in range(len(vertices)):
            for right in range(left + 1, len(vertices)):
                edge = LineString([vertices[left], vertices[right]])
                if allowed.covers(edge):
                    distance = edge.length
                    adjacency[left].append((distance, right))
                    adjacency[right].append((distance, left))
        distances = [math.inf] * len(vertices)
        previous: list[int | None] = [None] * len(vertices)
        distances[0] = 0
        queue = [(0.0, 0)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == 1:
                break
            for edge_length, neighbor in adjacency[node]:
                candidate = distance + edge_length
                if candidate < distances[neighbor]:
                    distances[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(queue, (candidate, neighbor))
        if not math.isfinite(distances[1]):
            raise PlanningError("No connector inside safe polygon between coverage segments")
        indices = [1]
        while indices[-1] != 0:
            predecessor = previous[indices[-1]]
            if predecessor is None:
                raise PlanningError("Safe connector reconstruction failed")
            indices.append(predecessor)
        return [vertices[index] for index in reversed(indices)]

    def _validate(
        self, polygon: Polygon, candidate: _Candidate, request: CoverageRequest
    ) -> PlanValidation:
        errors: list[str] = []
        allowed = polygon.buffer(1e-4)
        if len(candidate.path) > request.max_waypoints:
            errors.append(f"Waypoint count {len(candidate.path)} exceeds configured maximum {request.max_waypoints}")
        if not math.isfinite(candidate.path_length) or candidate.path_length <= 0:
            errors.append("Path length is invalid")
        for index, segment in enumerate(candidate.segments):
            coordinates = [(point.east_m, point.north_m) for point in segment.points_enu]
            if len(coordinates) < 2 or any(not math.isfinite(value) for point in coordinates for value in point):
                errors.append(f"Segment {index} has invalid coordinates")
                continue
            if not allowed.covers(LineString(coordinates)):
                errors.append(f"Segment {segment.segment_id} exits the safe polygon")
        # Square end caps model usable cross-track swath at the ends of each pass.
        # They avoid treating the sensor footprint as if it ended at the vehicle's
        # centre point while the intersection still clips coverage to the safe area.
        swaths = [
            sensor_footprint_swath(
                line, request.effective_swath_m, request.along_track_footprint_m,
            )
            for line in candidate.search_lines
        ]
        covered_geometry = unary_union(swaths).intersection(polygon)
        covered_area = covered_geometry.area
        percent = min(100.0, covered_area / polygon.area * 100)
        missed = max(0.0, polygon.area - covered_area)
        if percent + 1e-6 < request.min_planned_coverage_percent:
            errors.append(
                f"Planned coverage {percent:.2f}% is below configured minimum "
                f"{request.min_planned_coverage_percent:.2f}%"
            )
        return PlanValidation(
            valid=not errors,
            errors=errors,
            planned_coverage_percent=percent,
            covered_area_m2=covered_area,
            missed_area_m2=missed,
        )

    @staticmethod
    def _remove_adjacent_duplicates(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for point in points:
            if not result or Point(result[-1]).distance(Point(point)) > GEOMETRY_TOLERANCE_M:
                result.append(point)
        return result

    def _remove_redundant(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        points = self._remove_adjacent_duplicates(points)
        if len(points) <= 2:
            return points
        result = [points[0]]
        for point in points[1:-1]:
            if LineString([result[-1], points[-1]]).distance(Point(point)) > GEOMETRY_TOLERANCE_M:
                result.append(point)
        result.append(points[-1])
        return result
