from __future__ import annotations

from pymavlink import mavutil

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder


def make_plan():
    origin = GeoPoint(latitude=12.9716, longitude=77.5946, altitude_m=0)
    frame = LocalCoordinateFrame(origin)
    vertices = [frame.to_wgs84(EnuPoint(east, north, 0)) for east, north in [(0, 0), (50, 0), (50, 35), (0, 35)]]
    boundary = Boundary(
        vertices=vertices,
        source_format="test",
        area_m2=1750,
        area_hectares=0.175,
        origin=origin,
    )
    return CoveragePlanner().plan(
        "mission-builder",
        boundary,
        CoverageRequest(
            search_altitude_m=15,
            lane_spacing_m=10,
            effective_swath_m=10,
            edge_margin_m=1,
            search_speed_m_s=4,
        ),
    )


def test_arducopter_mission_order_integer_coordinates_and_rtl() -> None:
    plan = make_plan()
    mission = ArduCopterMissionBuilder().build(plan)

    assert mission.items[0].command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT  # ArduPilot home slot
    assert mission.items[1].command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
    assert mission.items[2].command == mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED
    assert mission.items[-1].command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
    assert [item.seq for item in mission.items] == list(range(len(mission.items)))
    assert all(item.mission_type == mavutil.mavlink.MAV_MISSION_TYPE_MISSION for item in mission.items)
    waypoints = [
        item for item in mission.items
        if item.command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT and item.seq > 0
    ]
    assert waypoints
    assert all(item.frame == mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT for item in waypoints)
    assert all(item.z == 15 for item in waypoints)
    assert all(abs(item.x) > 1_000_000 and abs(item.y) > 1_000_000 for item in waypoints)
    assert len(mission.fingerprint) == 64


def test_builder_is_deterministic() -> None:
    plan = make_plan()
    builder = ArduCopterMissionBuilder()
    assert builder.build(plan).fingerprint == builder.build(plan).fingerprint


def test_fleet_mission_can_defer_rtl_to_coordinated_landing_gate() -> None:
    mission = ArduCopterMissionBuilder().build(make_plan(), include_rtl=False)
    assert mission.items[-1].command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT
    assert all(
        item.command != mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
        for item in mission.items
    )
