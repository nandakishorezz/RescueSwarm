from __future__ import annotations

import pytest

from ground.backend.app.planning.models import (
    CoveragePlan,
    CoverageRequest,
    CoverageSegment,
    EnuCoordinate,
    PathKind,
    PlanValidation,
)
from datetime import UTC, datetime, timedelta

from pymavlink import mavutil

from ground.backend.app.domain.vehicle import VehicleState
from ground.backend.app.sitl.runner import (
    execution_timing_for_plan,
    grounded_simulation_settle_is_fresh,
    landing_is_confirmed,
    near_ground_descent_is_fresh,
    rtl_landing_timeout_for_state,
    search_deadline_expired,
)
from ground.backend.app.sitl.telemetry import SITLTelemetrySnapshot


def _plan(path: list[tuple[float, float]], speed_m_s: float = 5.0) -> CoveragePlan:
    points = [EnuCoordinate(east_m=east, north_m=north) for east, north in path]
    return CoveragePlan(
        mission_id="timing-test",
        planner_version="test",
        configuration=CoverageRequest(
            edge_margin_m=0,
            effective_swath_m=25,
            lane_spacing_m=20,
            search_altitude_m=30,
            search_speed_m_s=speed_m_s,
        ),
        safe_polygon_enu=points[:3],
        safe_polygon_wgs84=[],
        sweep_angle_deg=0,
        segments=[CoverageSegment(segment_id="search", kind=PathKind.SEARCH, points_enu=points)],
        path_points_enu=points,
        path_points_wgs84=[],
        estimated_path_length_m=558.6,
        estimated_search_time_s=111.7,
        number_of_search_lines=5,
        number_of_turns=4,
        cost_m=598.6,
        validation=PlanValidation(valid=True, planned_coverage_percent=99, covered_area_m2=1, missed_area_m2=0),
    )


def test_camera_world_budget_is_derived_from_the_actual_plan_not_a_fixed_600_seconds() -> None:
    timing = execution_timing_for_plan(
        _plan([(0, 0), (104, 0), (127, 0), (240, 0), (263, 0), (381, 0)]),
        configured_timeout_s=600,
    )

    # The observed 558.6m Phase-13 plan reached 71.9% while still advancing
    # at the old fixed 600s deadline.  The subsequent 424 m camera-world run
    # measured a worst valid leg slowdown of 10.2x, so the finite allowance is
    # 10.5 * 111.7s plus 120s of launch/transition margin.
    assert timing.execution_timeout_s == pytest.approx(1292.85)
    assert timing.execution_timeout_s > 600


def test_no_progress_watchdog_remains_shorter_than_the_plan_execution_budget() -> None:
    timing = execution_timing_for_plan(
        _plan([(0, 0), (118, 0), (236, 0), (354, 0)]),
        configured_timeout_s=600,
    )

    assert timing.no_progress_timeout_s == pytest.approx(248.8)
    assert timing.no_progress_timeout_s < timing.execution_timeout_s


def test_completed_search_enters_the_separate_bounded_rtl_phase() -> None:
    assert search_deadline_expired(1014.0, 1013.0, rtl_seen=False)
    assert not search_deadline_expired(1014.0, 1013.0, rtl_seen=True)


def _landing_snapshot(*, age_s: float, altitude_m: float, vertical_speed_m_s: float, landed_state: int) -> SITLTelemetrySnapshot:
    now = datetime.now(UTC)
    return SITLTelemetrySnapshot(
        vehicle=VehicleState(
            vehicle_id="drone_01", name="SITL", latitude=1, longitude=1,
            relative_altitude_m=altitude_m, vertical_speed_m_s=vertical_speed_m_s,
            ground_speed_m_s=0.02, roll_deg=0.1, pitch_deg=-0.2,
            flight_mode="LAND", armed=True,
        ),
        landed_state=landed_state,
        last_global_position=now - timedelta(seconds=age_s),
    )


def test_fresh_slow_near_ground_descent_gets_one_settle_opportunity() -> None:
    snapshot = _landing_snapshot(
        age_s=0.1, altitude_m=4.013, vertical_speed_m_s=-0.46,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    assert near_ground_descent_is_fresh(snapshot)


def test_stable_gazebo_contact_is_eligible_for_simulation_only_disarm() -> None:
    snapshot = _landing_snapshot(
        age_s=0.1, altitude_m=1.72, vertical_speed_m_s=0.0,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    assert grounded_simulation_settle_is_fresh(
        snapshot, home_latitude=1.0, home_longitude=1.0,
    )


def test_gusting_world_touchdown_within_gnss_gate_is_accepted_but_not_beyond_it() -> None:
    snapshot = _landing_snapshot(
        age_s=0.1, altitude_m=0.41, vertical_speed_m_s=0.0,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    snapshot.vehicle.longitude = 1.0 + 4.8 / 111_320.0
    assert grounded_simulation_settle_is_fresh(
        snapshot, home_latitude=1.0, home_longitude=1.0,
    )
    snapshot.vehicle.longitude = 1.0 + 6.2 / 111_320.0
    assert not grounded_simulation_settle_is_fresh(
        snapshot, home_latitude=1.0, home_longitude=1.0,
    )


@pytest.mark.parametrize("change", ["stale", "moving", "far", "tilted", "not_landing"])
def test_grounded_simulation_settle_rejects_unsafe_evidence(change: str) -> None:
    snapshot = _landing_snapshot(
        age_s=3.0 if change == "stale" else 0.1,
        altitude_m=1.72,
        vertical_speed_m_s=0.3 if change == "moving" else 0.0,
        landed_state=(
            mavutil.mavlink.MAV_LANDED_STATE_IN_AIR
            if change == "not_landing" else mavutil.mavlink.MAV_LANDED_STATE_LANDING
        ),
    )
    if change == "tilted":
        snapshot.vehicle.roll_deg = 8.0
    assert not grounded_simulation_settle_is_fresh(
        snapshot,
        home_latitude=1.0,
        home_longitude=1.001 if change == "far" else 1.0,
    )


def test_fresh_ardupilot_landing_plateau_gets_one_bounded_land_retry() -> None:
    snapshot = _landing_snapshot(
        age_s=0.1, altitude_m=1.83, vertical_speed_m_s=0.02,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    assert near_ground_descent_is_fresh(snapshot)


@pytest.mark.parametrize(
    "age_s,altitude_m,vertical_speed_m_s",
    [
        (3.0, 4.0, -0.4),
        (0.1, 10.0, -0.4),
        (0.1, 4.0, 0.0),
        (0.1, 1.0, 0.5),
    ],
)
def test_stale_enroute_or_stalled_descent_does_not_extend_landing(
    age_s: float, altitude_m: float, vertical_speed_m_s: float,
) -> None:
    snapshot = _landing_snapshot(
        age_s=age_s, altitude_m=altitude_m, vertical_speed_m_s=vertical_speed_m_s,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    assert not near_ground_descent_is_fresh(snapshot)


def test_fresh_multidrone_slow_near_ground_descent_up_to_eight_meters_is_fresh() -> None:
    snapshot = _landing_snapshot(
        age_s=0.1, altitude_m=5.495, vertical_speed_m_s=-0.55,
        landed_state=mavutil.mavlink.MAV_LANDED_STATE_LANDING,
    )
    assert near_ground_descent_is_fresh(snapshot)


def test_only_real_on_ground_state_confirms_landing() -> None:
    assert landing_is_confirmed(mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND)
    assert not landing_is_confirmed(mavutil.mavlink.MAV_LANDED_STATE_LANDING)


def test_rtl_budget_accounts_for_real_return_distance_and_descent() -> None:
    timeout = rtl_landing_timeout_for_state(
        home_latitude=12.9724015, home_longitude=77.5932950,
        vehicle_latitude=12.9728465, vehicle_longitude=77.5918682,
        relative_altitude_m=30.0, horizontal_speed_m_s=5.0,
        configured_timeout_s=180.0,
    )

    # The failed live run began RTL 162.5 m from home at 30 m.  Its fixed
    # 180 s guard expired during a healthy descent at 16.4 m; the work-derived
    # bound is finite but covers the measured four-times camera-world slowdown.
    assert timeout == pytest.approx(342.51, abs=0.2)
    assert timeout > 180.0


def test_short_rtl_keeps_the_configured_guard() -> None:
    assert rtl_landing_timeout_for_state(
        home_latitude=1.0, home_longitude=1.0,
        vehicle_latitude=1.0, vehicle_longitude=1.00001,
        relative_altitude_m=3.0, horizontal_speed_m_s=5.0,
        configured_timeout_s=180.0,
    ) == 180.0
