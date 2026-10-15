from __future__ import annotations

import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ground.backend.app.config import load_ground_config
from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.main import create_app
from ground.backend.app.services.coordinates import EnuPoint
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter, SITLCommandError
from rescue_core.simulation.georef import GazeboPoint, SimulationGeoreference
from rescue_core.simulation.orchestration import SimulationManager, SimulationRunDefinition, SimulationState
from rescue_core.simulation.profiles import (
    CameraProfile,
    MissionWorldProfile,
    Provenance,
    ScalarValue,
    SimulationConfig,
    SurvivorPlacement,
    SurvivorScenario,
    load_camera_profile,
    load_drone_profile,
    load_environment_profile,
)
from rescue_core.simulation.wind import generate_wind_schedule
from rescue_core.simulation.world import GazeboVehicleModelBuilder, WorldBuilder
from rescue_core.simulation.world.builders import _camera_link_euler

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "simulation" / "profiles"


def profiles():
    return (
        load_drone_profile(PROFILES / "rescueswarm_reference_quad.yaml"),
        load_camera_profile(PROFILES / "ov9281_reference_simulation.yaml"),
        load_environment_profile(PROFILES / "moderate_gust_environment.yaml"),
    )


def definition(run_id: str = "phase1_test") -> SimulationRunDefinition:
    drone, camera, environment = profiles()
    origin = GeoPoint(latitude=17.385, longitude=78.4867, altitude_m=520)
    georef = SimulationGeoreference(origin)
    corners = [EnuPoint(east_m=e, north_m=n, up_m=0) for e, n in ((-35, -25), (35, -25), (35, 25), (-35, 25))]
    boundary = [georef.local.to_wgs84(point) for point in corners]
    survivor_points = [georef.local.to_wgs84(EnuPoint(east_m=e, north_m=n, up_m=0)) for e, n in ((8, 7), (-14, 12), (18, -9))]
    mission = MissionWorldProfile(
        mission_id="phase1_world", boundary_wgs84=[(p.latitude, p.longitude) for p in boundary],
        origin_wgs84=(origin.latitude, origin.longitude, origin.altitude_m or 0),
        ground_elevation_m=ScalarValue(value=0, unit="m", provenance=Provenance.SIMULATION_ONLY),
        world_dimensions_m={"value": [120, 100, 80], "unit": "m", "provenance": "ENGINEERING_ESTIMATE"},
        launch_zone_width_m={"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
        launch_zone_length_m={"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
        environment_profile_id=environment.profile_id,
    )
    survivor_scenario = SurvivorScenario(
        scenario_id="phase1_hidden",
        survivors=[SurvivorPlacement(survivor_id=f"hidden_{index}", latitude=p.latitude, longitude=p.longitude, body_state="lying" if index == 2 else "standing") for index, p in enumerate(survivor_points, 1)],
    )
    config = SimulationConfig(
        config_id="phase1_config", drone_profile_id=drone.profile_id, camera_profile_id=camera.camera_id,
        environment_profile_id=environment.profile_id, mission_world_profile_id=mission.mission_id,
        survivor_scenario_id=survivor_scenario.scenario_id, random_seed=environment.random_seed,
    )
    return SimulationRunDefinition(run_id=run_id, config=config, drone=drone, camera=camera, environment=environment, mission=mission, survivors=survivor_scenario)


@pytest.mark.stage13sim
def test_reference_profiles_are_explicitly_unverified_and_physically_valid():
    drone, camera, _ = profiles()
    assert drone.rotor_count == len(drone.rotors) == 4
    assert len({rotor.position_body_frd.value for rotor in drone.rotors}) == 4
    assert drone.mass_kg.provenance == Provenance.REFERENCE_PRESET
    assert camera.mount_translation_body_frd.provenance == Provenance.PHYSICAL_UNVERIFIED
    assert camera.intrinsics and camera.intrinsics.fx.provenance == Provenance.ENGINEERING_ESTIMATE
    with pytest.raises(ValidationError, match="positive"):
        drone.model_copy(update={"mass_kg": {"value": 0, "unit": "kg", "provenance": "SIMULATION_ONLY"}}).__class__.model_validate({**drone.model_dump(), "mass_kg": {"value": 0, "unit": "kg", "provenance": "SIMULATION_ONLY"}})


@pytest.mark.stage13sim
def test_camera_intrinsics_are_authoritative_for_generated_gazebo_fov():
    drone, camera, _ = profiles()
    expected_hfov = 2 * math.atan(camera.resolution_width / (2 * camera.intrinsics.fx.value))  # type: ignore[union-attr]
    root = ET.fromstring(GazeboVehicleModelBuilder().build(drone, camera))
    assert float(root.findtext(".//sensor[@type='camera']/camera/horizontal_fov")) == pytest.approx(expected_hfov, abs=1e-10)
    assert int(root.findtext(".//sensor[@type='camera']/camera/image/width")) == camera.resolution_width
    assert float(root.findtext(".//sensor[@type='camera']/update_rate")) == camera.fps.value
    # The active camera-optical-to-body transform becomes the Gazebo link pose.
    _, pitch, _ = _camera_link_euler(camera.mount_rotation_camera_to_body.value)
    assert pitch == pytest.approx(math.pi / 2, abs=1e-9)


@pytest.mark.stage13sim
def test_generated_camera_scene_has_explicit_headless_sensor_lighting() -> None:
    drone, camera, environment = profiles()
    item = definition("lighting-test")
    world = ET.fromstring(WorldBuilder().build(
        item.mission, drone, environment.wind, item.survivors,
    ))
    assert world.findtext(".//light[@name='sun']/diffuse") == "1 1 1 1"
    assert world.findtext(".//scene/ambient") == "0.80 0.80 0.80 1"
    assert world.findtext(".//model[@name='flat_ground']//material/ambient") == "0.40 0.58 0.34 1"
    survivor_ambient = [
        material.findtext("ambient")
        for model in world.findall(".//model")
        if model.attrib.get("name", "").startswith("survivor_")
        for material in model.findall(".//material")
    ]
    assert survivor_ambient.count("0.95 0.28 0.05 1") >= 1


@pytest.mark.stage13sim
def test_georeference_round_trip_and_explicit_axis_contract():
    origin = GeoPoint(latitude=17.385, longitude=78.4867, altitude_m=520)
    frame = SimulationGeoreference(origin)
    point = EnuPoint(east_m=25, north_m=15, up_m=3)
    gazebo = frame.enu_to_gazebo(point)
    assert gazebo == GazeboPoint(x_m=25, y_m=15, z_m=3)
    restored = frame.gazebo_to_enu(gazebo)
    assert (restored.east_m, restored.north_m, restored.up_m) == pytest.approx((25, 15, 3), abs=1e-9)
    round_trip = frame.local.to_enu(frame.local.to_wgs84(point))
    assert (round_trip.east_m, round_trip.north_m, round_trip.up_m) == pytest.approx((25, 15, 3), abs=1e-5)


@pytest.mark.stage13sim
def test_wind_schedule_is_seeded_and_not_a_pose_animation():
    wind = profiles()[2].wind
    first = generate_wind_schedule(wind, duration_s=20, step_s=.25)
    second = generate_wind_schedule(wind, duration_s=20, step_s=.25)
    changed = wind.model_copy(update={"random_seed": wind.random_seed + 1})
    third = generate_wind_schedule(changed, duration_s=20, step_s=.25)
    assert first == second
    assert first != third
    assert all(set(sample.model_dump()) == {"sim_time_s", "east_mps", "north_mps", "up_mps", "gust_active"} for sample in first)


@pytest.mark.stage13sim
def test_world_has_visible_survivors_camera_and_physics_wind():
    item = definition()
    world = WorldBuilder().build(item.mission, item.drone, item.environment.wind, item.survivors)
    root = ET.fromstring(world)
    assert len(root.findall(".//model[@name='survivor_hidden_1']")) == 1
    assert root.find(".//plugin[@name='gz::sim::systems::WindEffects']") is not None
    assert root.find(".//wind/linear_velocity") is not None
    vehicle = ET.fromstring(GazeboVehicleModelBuilder().build(item.drone, item.camera))
    assert vehicle.find(".//sensor[@type='camera']") is not None
    assert all(link.findtext("enable_wind") == "true" for link in vehicle.findall(".//link") if link.attrib["name"].startswith("rotor_"))


@pytest.mark.stage13sim
def test_prepare_creates_isolated_truth_and_reproducible_fingerprint(tmp_path):
    manager = SimulationManager(tmp_path)
    item = definition("first")
    run_dir = manager.prepare(item)
    assert manager.state == SimulationState.READY
    assert (run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").is_file()
    for alias in ("survivor_ground_truth.json", "wind_truth.jsonl", "vehicle_truth.jsonl"):
        redirect = json.loads((run_dir / alias).read_text())
        assert redirect["contains_ground_truth"] is False
        assert redirect["autonomy_ingestion_permitted"] is False
        assert redirect["observer_only_artifact"].startswith("SIMULATION_ONLY_GROUND_TRUTH_")
    status = json.dumps(manager.status())
    assert "position_wgs84" not in status and "ground_truth" not in status
    fingerprint = json.loads((run_dir / "simulation_config.json").read_text())["simulation_fingerprint"]
    assert fingerprint == item.config.fingerprint(item.drone, item.camera, item.environment, item.mission, item.survivors)
    assert len(json.loads((run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").read_text())["survivors"]) == 3


@pytest.mark.stage13sim
def test_truth_namespace_has_no_import_path_into_frozen_autonomy():
    prohibited = [ROOT / "rescue_core" / name for name in ("autonomy", "vision")]
    prohibited += [ROOT / "ground" / "backend" / "app" / name for name in ("fleet", "planning", "services", "sitl")]
    for root in prohibited:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "simulation.truth" not in text and "simulation_ground_truth" not in text.lower(), path


@pytest.mark.stage13sim
def test_simulation_api_truth_is_opt_in_and_competition_forbidden(tmp_path):
    config = load_ground_config(ROOT / "config" / "ground.yaml")
    test_config = config.model_copy(update={"database": config.database.model_copy(update={"url": f"sqlite:///{tmp_path / 'lab.db'}"})})
    with TestClient(create_app(test_config)) as client:
        normal = client.get("/api/simulation/status")
        assert normal.status_code == 200 and "ground_truth" not in normal.text
        assert client.get("/api/simulation/observer/truth").status_code == 403
        assert "ground_truth" not in client.get("/api/missions").text.lower()
    competition = test_config.model_copy(update={"competition": test_config.competition.model_copy(update={"enabled": True}), "database": test_config.database.model_copy(update={"url": f"sqlite:///{tmp_path / 'competition.db'}"})})
    with TestClient(create_app(competition)) as client:
        assert client.get("/api/simulation/status").status_code == 403
        assert client.get("/api/simulation/observer/truth?observer_ground_truth=true").status_code == 403


@pytest.mark.stage13sim
def test_placing_survivors_does_not_construct_autonomy_targets(tmp_path):
    item = definition("truth_firewall")
    SimulationManager(tmp_path).prepare(item)
    assert all(type(survivor).__name__ == "SurvivorPlacement" for survivor in item.survivors.survivors)
    assert not any(key in item.survivors.model_dump_json() for key in ("TargetObservation", "CanonicalTarget", "delivery_assignment"))


@pytest.mark.stage13sim
def test_forced_disarm_is_guarded_by_simulation_authority_and_ground_evidence():
    adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only")
    with pytest.raises(SITLCommandError, match="grounded evidence"):
        adapter.force_disarm_grounded_simulation(grounded_evidence=False)
    adapter.command_authority = "physical"
    with pytest.raises(SITLCommandError, match="simulation_only"):
        adapter.force_disarm_grounded_simulation(grounded_evidence=True)
