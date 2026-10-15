from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Point, Polygon

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner, PlanningError
from ground.backend.app.planning.models import CoverageRequest, PathKind, SweepStrategy
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.coverage import ActualCoverageTracker


ORIGIN = GeoPoint(latitude=12.9716, longitude=77.5946, altitude_m=0)


def boundary_from_enu(points: list[tuple[float, float]]) -> Boundary:
    frame = LocalCoordinateFrame(ORIGIN)
    vertices = [frame.to_wgs84(EnuPoint(east, north, 0)) for east, north in points]
    area = Polygon(points).area
    return Boundary(
        vertices=vertices,
        source_format="test",
        area_m2=area,
        area_hectares=area / 10_000,
        origin=ORIGIN,
    )


def request(**overrides: object) -> CoverageRequest:
    values: dict[str, object] = {
        "search_altitude_m": 15,
        "lane_spacing_m": 10,
        "effective_swath_m": 10,
        "edge_margin_m": 2,
        "search_speed_m_s": 4,
        "turn_penalty_m": 3,
        "min_planned_coverage_percent": 98,
    }
    values.update(overrides)
    return CoverageRequest(**values)


def segment_line(segment: object) -> LineString:
    return LineString([(point.east_m, point.north_m) for point in segment.points_enu])


def test_rectangle_plan_is_boustrophedon_valid_and_covered() -> None:
    plan = CoveragePlanner().plan("rectangle", boundary_from_enu([(0, 0), (80, 0), (80, 50), (0, 50)]), request())
    search = [segment for segment in plan.segments if segment.kind is PathKind.SEARCH]
    directions = [math.copysign(1, segment.points_enu[-1].east_m - segment.points_enu[0].east_m) for segment in search]

    assert plan.validation.valid
    assert plan.validation.planned_coverage_percent >= 98
    assert len(search) >= 4
    assert all(left != right for left, right in zip(directions, directions[1:]))
    safe = Polygon([(point.east_m, point.north_m) for point in plan.safe_polygon_enu]).buffer(1e-4)
    assert all(safe.covers(segment_line(segment)) for segment in plan.segments)


def test_footprint_aware_lane_distribution_covers_oblique_edges() -> None:
    # This shape and 20 m / 25 m ratio reproduce the live plan's former
    # oblique 150.86 m2 corner gap.  Coverage is measured over the explicitly
    # inset searchable polygon, not the original geofence.
    boundary = boundary_from_enu([(-52, 70), (78, -27), (27, -66), (-77, 26)])
    plan = CoveragePlanner().plan(
        "oblique",
        boundary,
        request(
            search_altitude_m=30,
            lane_spacing_m=20,
            effective_swath_m=25,
            edge_margin_m=5,
            min_planned_coverage_percent=99.99,
        ),
    )

    assert plan.validation.planned_coverage_percent == pytest.approx(100.0, abs=1e-6)
    assert plan.validation.missed_area_m2 == pytest.approx(0.0, abs=1e-6)

    tracker = ActualCoverageTracker(plan, ORIGIN)
    for point in plan.path_points_wgs84:
        tracker.add(point, searching=True)
    assert tracker.metrics()["actual_coverage_percent"] == pytest.approx(100.0, abs=1e-4)


def test_rectangular_camera_footprint_covers_full_unshrunk_search_polygon() -> None:
    boundary = boundary_from_enu([(-52, 70), (78, -27), (27, -66), (-77, 26)])
    plan = CoveragePlanner().plan(
        "v3-envelope",
        boundary,
        request(
            search_altitude_m=8,
            lane_spacing_m=6.4,
            effective_swath_m=8.0,
            along_track_footprint_m=12.8,
            edge_margin_m=0,
            min_planned_coverage_percent=99.99,
        ),
    )

    assert plan.metadata["safe_area_m2"] == pytest.approx(plan.metadata["boundary_area_m2"], abs=0.01)
    assert plan.validation.planned_coverage_percent == pytest.approx(100.0, abs=1e-6)
    tracker = ActualCoverageTracker(plan, ORIGIN)
    for point in plan.path_points_wgs84:
        tracker.add(point, searching=True)
    assert tracker.metrics()["actual_coverage_percent"] == pytest.approx(100.0, abs=1e-4)


def test_configured_overlap_provides_edge_reserve_for_path_following_error() -> None:
    """The 50% overlap budget covers a measured-scale lateral route offset."""
    plan = CoveragePlanner().plan(
        "edge-reserve",
        boundary_from_enu([(0, 0), (80, 0), (80, 50), (0, 50)]),
        request(
            search_altitude_m=8,
            lane_spacing_m=4,
            effective_swath_m=8,
            edge_margin_m=0,
            fixed_sweep_angle_deg=0,
            sweep_strategy=SweepStrategy.FIXED_ANGLE,
            min_planned_coverage_percent=99.99,
        ),
    )
    tracker = ActualCoverageTracker(plan, ORIGIN)
    frame = LocalCoordinateFrame(ORIGIN)
    # Apply a systematic 2.5 m cross-track error to every flown point.  The
    # two overlap-derived guards cover it without changing the denominator.
    for point in plan.path_points_enu:
        tracker.add(frame.to_wgs84(EnuPoint(point.east_m, point.north_m + 2.5, 8)), searching=True)

    assert plan.validation.planned_coverage_percent == pytest.approx(100.0, abs=1e-6)
    assert tracker.metrics()["actual_coverage_percent"] == pytest.approx(100.0, abs=1e-4)
    lane_offsets = sorted(
        round(segment.points_enu[0].north_m, 6)
        for segment in plan.segments if segment.kind is PathKind.SEARCH
    )
    # Edge guards are added without moving the original half-footprint passes.
    assert lane_offsets[:3] == pytest.approx([1.0, 2.0, 4.0], abs=1e-4)
    assert lane_offsets[-3:] == pytest.approx([46.0, 48.0, 49.0], abs=1e-4)


def test_rotated_long_rectangle_uses_efficient_orientation() -> None:
    rectangle = Polygon([(-60, -10), (60, -10), (60, 10), (-60, 10)])
    rotated = list(__import__("shapely").affinity.rotate(rectangle, 32, origin=(0, 0)).exterior.coords)[:-1]
    plan = CoveragePlanner().plan("rotated", boundary_from_enu(rotated), request(edge_margin_m=1))

    delta = abs(((plan.sweep_angle_deg - 32 + 90) % 180) - 90)
    assert delta < 1
    assert plan.number_of_search_lines <= 3


def test_optimizer_beats_materially_worse_fixed_orientation() -> None:
    boundary = boundary_from_enu([(0, 0), (150, 0), (150, 30), (0, 30)])
    planner = CoveragePlanner()
    automatic = planner.plan("auto", boundary, request(edge_margin_m=1))
    fixed = planner.plan(
        "fixed",
        boundary,
        request(edge_margin_m=1, sweep_strategy=SweepStrategy.FIXED_ANGLE, fixed_sweep_angle_deg=90),
    )

    assert automatic.cost_m < fixed.cost_m * 0.80
    assert automatic.number_of_search_lines < fixed.number_of_search_lines


def test_concave_polygon_connectors_never_leave_safe_region() -> None:
    boundary = boundary_from_enu([(0, 0), (80, 0), (80, 25), (35, 25), (35, 70), (0, 70)])
    plan = CoveragePlanner().plan("concave", boundary, request(edge_margin_m=1, lane_spacing_m=8, effective_swath_m=8))
    safe = Polygon([(point.east_m, point.north_m) for point in plan.safe_polygon_enu]).buffer(1e-4)

    assert any(segment.kind is PathKind.TRANSIT for segment in plan.segments)
    assert all(safe.covers(segment_line(segment)) for segment in plan.segments)


def test_edge_margin_that_collapses_polygon_fails() -> None:
    with pytest.raises(PlanningError, match="collapses"):
        CoveragePlanner().plan("tiny", boundary_from_enu([(0, 0), (3, 0), (3, 3), (0, 3)]), request(edge_margin_m=2))


@pytest.mark.parametrize("field,value", [("lane_spacing_m", 0), ("search_altitude_m", 0), ("search_speed_m_s", -1)])
def test_invalid_engineering_inputs_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        request(**{field: value})


def test_plan_is_deterministic() -> None:
    boundary = boundary_from_enu([(0, 0), (90, 0), (75, 55), (10, 65)])
    first = CoveragePlanner().plan("same", boundary, request())
    second = CoveragePlanner().plan("same", boundary, request())

    assert first.sweep_angle_deg == second.sweep_angle_deg
    assert first.path_points_enu == second.path_points_enu
    assert first.estimated_path_length_m == pytest.approx(second.estimated_path_length_m)


def test_unattainable_coverage_threshold_stops_plan() -> None:
    with pytest.raises(PlanningError, match="coverage"):
        CoveragePlanner().plan(
            "coverage",
            boundary_from_enu([(0, 0), (40, 0), (40, 40), (0, 40)]),
            request(lane_spacing_m=15, effective_swath_m=5, min_planned_coverage_percent=98),
        )


def test_wgs84_enu_path_round_trip_remains_precise() -> None:
    boundary = boundary_from_enu([(0, 0), (60, 0), (60, 40), (0, 40)])
    plan = CoveragePlanner().plan("roundtrip", boundary, request())
    frame = LocalCoordinateFrame(boundary.origin)

    for expected, geographic in zip(plan.path_points_enu, plan.path_points_wgs84):
        actual = frame.to_enu(geographic)
        assert Point(expected.east_m, expected.north_m).distance(Point(actual.east_m, actual.north_m)) < 0.01
