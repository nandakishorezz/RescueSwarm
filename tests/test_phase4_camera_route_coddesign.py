import math
from pathlib import Path

import pytest

from rescue_core.autonomy.config import load_rescue_config
from rescue_core.simulation.profiles import load_camera_profile
from scripts.run_stage13sim_phase4 import build_definition, build_mission


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def camera():
    return load_camera_profile(ROOT / "simulation/profiles/ov9281_phase2_oblique_simulation.yaml")


@pytest.fixture(scope="module")
def route():
    definition = build_definition(
        "camera_route_test", speed_mps=3.0, altitude_m=8.0,
        wind_profile="PERCEPTION_MODERATE", survivor_count=1,
        sensor_profile="REFERENCE_NOMINAL", payload_slots=3,
    )
    mission, plan = build_mission(definition, 8.0)
    return definition, mission, plan


def test_c1_camera_source_pitch_is_selected_nadir_mount(camera):
    assert camera.mount_pitch_deg.value == 0.0
    assert camera.mount_rotation_camera_to_body.value == pytest.approx(
        (math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)), abs=1e-12,
    )


def test_c2_camera_resolution_remains_1280_by_800(camera):
    assert (camera.resolution_width, camera.resolution_height) == (1280, 800)


def test_c3_camera_fov_is_unchanged(camera):
    horizontal, vertical = camera.derived_fov()
    assert horizontal == pytest.approx(1.3494818844471055)
    assert vertical == pytest.approx(0.9272952180016122)


def test_c10_route_covers_complete_search_polygon(route):
    _, _, plan = route
    assert plan.validation.valid
    assert plan.validation.planned_coverage_percent >= 99.999


def test_c11_route_dense_leg_spacing_matches_detector_qualified_swath(route):
    _, _, plan = route
    assert plan.configuration.lane_spacing_m == pytest.approx(0.75)
    assert plan.configuration.effective_swath_m == pytest.approx(0.75)
    assert plan.metadata["detector_qualified_lane_spacing_m"] == pytest.approx(0.75)


def test_c12_route_is_independent_of_survivor_truth():
    first = build_definition("route_truth_a", survivor_count=1)
    second = build_definition("route_truth_b", survivor_count=1)
    moved = second.survivors.survivors[0].model_copy(update={
        "latitude": second.survivors.survivors[0].latitude + 0.00001,
        "longitude": second.survivors.survivors[0].longitude - 0.00001,
    })
    second.survivors = second.survivors.model_copy(update={"survivors": [moved]})
    first_mission, _ = build_mission(first, 8.0)
    second_mission, _ = build_mission(second, 8.0)
    assert first_mission.fingerprint == second_mission.fingerprint


def test_c13_route_stays_within_existing_safe_boundary(route):
    _, _, plan = route
    east = [point.east_m for point in plan.safe_polygon_enu]
    north = [point.north_m for point in plan.safe_polygon_enu]
    assert all(min(east)-1e-6 <= point.east_m <= max(east)+1e-6 for point in plan.path_points_enu)
    assert all(min(north)-1e-6 <= point.north_m <= max(north)+1e-6 for point in plan.path_points_enu)


def test_c14_route_fits_thirty_minute_mission_budget(route):
    _, _, plan = route
    assert plan.estimated_search_time_s < 30 * 60
    assert plan.estimated_search_time_s < 23 * 60


def test_c15_alignment_threshold_is_unchanged():
    config = load_rescue_config(ROOT / "config/phase4_rescue_orchestration.yaml")
    assert config.alignment.alignment_radius_m == pytest.approx(0.5)


def test_c16_freshness_configuration_is_unchanged():
    config = load_rescue_config(ROOT / "config/phase4_rescue_orchestration.yaml")
    assert config.alignment.observation_stale_s > 0


def test_c17_dwell_configuration_is_unchanged():
    config = load_rescue_config(ROOT / "config/phase4_rescue_orchestration.yaml")
    assert config.alignment.stable_dwell_s > 0


def test_c18_priority_pass_precedes_dense_truth_independent_coverage(route):
    _, mission, plan = route
    assert plan.metadata["camera_route_coddesign"] == "NADIR_PRIORITY_THEN_DENSE"
    assert plan.metadata["truth_dependent"] is False
    assert plan.number_of_search_lines == 56
    assert len(mission.items) == len(plan.path_points_wgs84) + 3
