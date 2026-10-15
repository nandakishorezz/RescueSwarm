"""Generate and optionally execute the Stage 13SIM Phase-1 Gazebo gold run.

This runner never synthesizes detections.  The only live MAVLink reader is the
existing SITLAutopilotAdapter router; Gazebo truth is written to observer-only
artifacts and is never forwarded to autonomy.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from pymavlink import mavutil

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.services.coordinates import EnuPoint
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from rescue_core.simulation.artifacts import SimulationArtifactWriter
from rescue_core.simulation.georef import SimulationGeoreference
from rescue_core.simulation.gazebo import GazeboTruthObserver
from rescue_core.simulation.orchestration import SimulationManager, SimulationRunDefinition
from rescue_core.simulation.profiles import (
    MissionWorldProfile,
    ScalarValue,
    SimulationConfig,
    SurvivorPlacement,
    SurvivorScenario,
    Vector3Value,
    WindMode,
    load_camera_profile,
    load_drone_profile,
    load_environment_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def build_definition(run_id: str, *, wind_mode: str = "GUSTING", speed_mps: float = 4.0) -> SimulationRunDefinition:
    drone = load_drone_profile(ROOT / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
    camera = load_camera_profile(ROOT / "simulation" / "profiles" / "ov9281_reference_simulation.yaml")
    environment = load_environment_profile(ROOT / "simulation" / "profiles" / "moderate_gust_environment.yaml")
    if wind_mode == "CALM":
        wind = environment.wind.model_copy(update={
            "enabled": False, "mode": WindMode.CALM, "turbulence_enabled": False, "gusts_enabled": False,
            "mean_speed_mps": environment.wind.mean_speed_mps.model_copy(update={"value": 0.0}),
        })
        environment = environment.model_copy(update={"profile_id": "calm_phase1", "wind": wind})
    drone = drone.__class__.model_validate({
        **drone.model_dump(mode="json"),
        "cruise_speed_mps": {**drone.cruise_speed_mps.model_dump(mode="json"), "value": speed_mps},
    })
    boundary = BoundaryService(10).import_boundary(
        "small_easy.geojson", (ROOT / "data" / "fixtures" / "stage10s" / "small_easy.geojson").read_bytes()
    )
    assert boundary.origin is not None
    origin = GeoPoint(latitude=boundary.origin.latitude, longitude=boundary.origin.longitude, altitude_m=585)
    georef = SimulationGeoreference(origin)
    offsets = ((8.0, 7.0, "standing"), (-14.0, 12.0, "lying"), (18.0, -9.0, "standing"))
    survivors = []
    for index, (east, north, state) in enumerate(offsets, 1):
        point = georef.local.to_wgs84(EnuPoint(east_m=east, north_m=north, up_m=0))
        survivors.append(SurvivorPlacement(
            survivor_id=f"hidden_{index:02d}", latitude=point.latitude, longitude=point.longitude,
            body_state=state, visual_variant="PROCEDURAL_ORANGE",
        ))
    mission = MissionWorldProfile(
        mission_id="stage13sim_phase1_world",
        boundary_wgs84=[(item.latitude, item.longitude) for item in boundary.vertices],
        origin_wgs84=(origin.latitude, origin.longitude, origin.altitude_m or 0),
        ground_elevation_m=ScalarValue(value=0, unit="m", provenance="SIMULATION_ONLY"),
        world_dimensions_m=Vector3Value(value=(160, 140, 100), unit="m", provenance="ENGINEERING_ESTIMATE"),
        launch_zone_width_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        launch_zone_length_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        environment_profile_id=environment.profile_id,
    )
    scenario = SurvivorScenario(scenario_id="stage13sim_phase1_hidden", survivors=survivors)
    config = SimulationConfig(
        config_id="stage13sim_phase1_gold", drone_profile_id=drone.profile_id,
        camera_profile_id=camera.camera_id, environment_profile_id=environment.profile_id,
        mission_world_profile_id=mission.mission_id, survivor_scenario_id=scenario.scenario_id,
        random_seed=environment.random_seed,
    )
    return SimulationRunDefinition(
        run_id=run_id, config=config, drone=drone, camera=camera, environment=environment,
        mission=mission, survivors=scenario,
    )


def _flight_mission(definition: SimulationRunDefinition, *, short_route: bool = False):
    boundary = BoundaryService(10).import_boundary(
        "phase1.json", json.dumps({"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in definition.mission.boundary_wgs84] + [[definition.mission.boundary_wgs84[0][1], definition.mission.boundary_wgs84[0][0]]]]}).encode(),
    )
    plan = CoveragePlanner().plan(
        "stage13sim_phase1_gold",
        boundary,
        CoverageRequest(
            search_altitude_m=8, lane_spacing_m=100 if short_route else 35, effective_swath_m=100 if short_route else 35, edge_margin_m=5,
            search_speed_m_s=definition.drone.cruise_speed_mps.value,
            min_planned_coverage_percent=80, max_waypoints=100,
        ),
    )
    return ArduCopterMissionBuilder().build(plan, include_rtl=False), plan


def run_live(manager: SimulationManager, run_dir: Path, timeout_s: float, *, short_route: bool = False) -> dict[str, object]:
    writer = SimulationArtifactWriter(run_dir)
    truth_observer: GazeboTruthObserver | None = None
    started = time.monotonic()
    mode_history: list[str] = []
    estimates = 0
    armed_seen = False
    airborne_seen = False
    try:
        manager.start()
        assert manager.adapter and manager.definition
        adapter = manager.adapter
        adapter.wait_ready(90)
        mission, plan = _flight_mission(manager.definition, short_route=short_route)
        transfer, verification = adapter.upload_and_verify(mission)
        truth_observer = GazeboTruthObserver(
            "rescueswarm_stage13sim", manager.definition.drone.profile_id,
            run_dir / "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl",
        )
        truth_observer.start()
        adapter.prepare_for_arm(); adapter.arm(); adapter.start_auto_mission()
        last_saved = 0.0
        rtl_commanded = False
        mission_route_completed = False
        landed_since: float | None = None
        landed_evidence: str | None = None
        final_sequence = mission.items[-1].seq
        initial_boot_ms: int | None = None
        wall_safety_deadline = time.monotonic() + max(timeout_s * 4, 300)
        while time.monotonic() < wall_safety_deadline:
            heartbeat = adapter.router.latest("HEARTBEAT") if adapter.router else None
            position = adapter.router.latest("GLOBAL_POSITION_INT") if adapter.router else None
            extended = adapter.router.latest("EXTENDED_SYS_STATE") if adapter.router else None
            attitude = adapter.router.latest("ATTITUDE_QUATERNION") if adapter.router else None
            servo = adapter.router.latest("SERVO_OUTPUT_RAW") if adapter.router else None
            now = time.monotonic()
            if position is not None and initial_boot_ms is None:
                initial_boot_ms = int(position.time_boot_ms)
            simulation_elapsed_s = (int(position.time_boot_ms) - initial_boot_ms) / 1000 if position is not None and initial_boot_ms is not None else 0.0
            if position is not None and int(position.relative_alt) / 1000 > 1.0:
                airborne_seen = True
            if heartbeat:
                mode = mavutil.mode_string_v10(heartbeat)
                if not mode_history or mode_history[-1] != mode:
                    mode_history.append(mode)
                    writer.append_jsonl("events.jsonl", {"wall_elapsed_s": now - started, "type": "MODE_CHANGED", "mode": mode})
                armed = bool(int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                armed_seen = armed_seen or armed
                if armed_seen and not armed and rtl_commanded:
                    break
            reached = adapter.router.latest("MISSION_ITEM_REACHED") if adapter.router else None
            if reached is not None and int(reached.seq) == final_sequence and not rtl_commanded:
                mission_route_completed = True
                adapter.rtl(); rtl_commanded = True
                writer.append_jsonl("events.jsonl", {
                    "wall_elapsed_s": now - started, "type": "RTL_COMMANDED",
                    "evidence": f"final search sequence {int(reached.seq)} reached",
                })
            if simulation_elapsed_s >= timeout_s and not rtl_commanded:
                adapter.rtl(); rtl_commanded = True
                writer.append_jsonl("events.jsonl", {
                    "wall_elapsed_s": now - started, "simulation_elapsed_s": simulation_elapsed_s,
                    "type": "RTL_COMMANDED_ON_SIMULATION_TIMEOUT",
                })
            if airborne_seen and rtl_commanded and position and heartbeat and bool(int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
                # The generated reference model does not include a rangefinder and
                # ArduPilot's external-simulator EXTENDED_SYS_STATE can remain
                # UNKNOWN after the rigid body has settled.  Never infer landing
                # from elapsed time alone: require sustained zero relative altitude
                # and vertical velocity after an observed flight and real RTL.
                autopilot_on_ground = bool(extended) and int(extended.landed_state) == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
                horizontal_speed = math.hypot(int(position.vx) / 100, int(position.vy) / 100)
                kinematically_grounded = (
                    abs(int(position.relative_alt) / 1000) < 0.15
                    and abs(int(position.vz) / 100) < 0.12
                    and horizontal_speed < 0.25
                )
                landed = kinematically_grounded
                landed_since = landed_since or now if landed else None
                if landed_since is not None and now - landed_since >= 8.0:
                    landed_evidence = "AUTOPILOT_ON_GROUND" if autopilot_on_ground else "SUSTAINED_MAVLINK_GROUND_KINEMATICS"
                    adapter.force_disarm_grounded_simulation(grounded_evidence=True)
                    writer.append_jsonl("events.jsonl", {
                        "wall_elapsed_s": now - started, "type": "LANDED_DISARM_COMMANDED",
                        "evidence": landed_evidence,
                        "guard": "airborne previously observed + RTL + relative altitude <0.15 m + |vertical speed| <0.12 m/s + horizontal speed <0.25 m/s for 8 s",
                    })
            if now - last_saved >= .1 and position and attitude:
                payload = {
                    "classification": "AUTOPILOT_ESTIMATE", "wall_elapsed_s": now - started,
                    "simulation_elapsed_s": simulation_elapsed_s,
                    "wall_time": datetime.now(UTC).isoformat(),
                    "latitude": int(position.lat) / 1e7, "longitude": int(position.lon) / 1e7,
                    "relative_altitude_m": int(position.relative_alt) / 1000,
                    "landed_state": int(extended.landed_state) if extended else None,
                    "velocity_ned_mps": [int(position.vx) / 100, int(position.vy) / 100, int(position.vz) / 100],
                    "quaternion_body_to_ned": [float(attitude.q1), float(attitude.q2), float(attitude.q3), float(attitude.q4)],
                    "motor_outputs": [int(getattr(servo, f"servo{index}_raw", 0)) for index in range(1, 5)] if servo else None,
                }
                writer.append_jsonl("autopilot_estimate.jsonl", payload); estimates += 1; last_saved = now
            time.sleep(.03)
        if not rtl_commanded:
            adapter.rtl()
            rtl_commanded = True
        heartbeat = adapter.router.latest("HEARTBEAT") if adapter.router else None
        disarmed = bool(heartbeat) and not bool(int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        truth_metrics = truth_observer.stop() if truth_observer else {}
        truth_observer = None
        result = {
            "classification": "HIGH_FIDELITY_SOFTWARE_PHYSICS_SIMULATION",
            "passed": airborne_seen and mission_route_completed and disarmed and verification.valid and "AUTO" in mode_history and "RTL" in mode_history,
            "mode_history": mode_history, "mission_uploaded": transfer.success, "mission_verified": verification.valid,
            "planned_distance_m": plan.estimated_path_length_m, "requested_speed_mps": plan.configuration.search_speed_m_s,
            "autopilot_estimate_samples": estimates, "rtl": "RTL" in mode_history, "disarmed": disarmed,
            "airborne_observed": airborne_seen,
            "auto_route_completed": mission_route_completed,
            "landing_evidence": landed_evidence,
            "autonomy_target_count": 0, "detection_pipeline_connected": False, "hardware_gate_h1": "PENDING",
            **truth_metrics,
        }
        writer.write_json("run_summary.json", result)
        return result
    finally:
        if truth_observer:
            truth_observer.stop()
        manager.stop(completed=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=300)
    parser.add_argument("--run-id", default=f"stage13sim_phase1_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--wind-mode", choices=("GUSTING", "CALM"), default="GUSTING")
    parser.add_argument("--speed-mps", type=float, default=4.0)
    parser.add_argument("--short-route", action="store_true", help="Use a one-sweep coupling route for speed qualification")
    args = parser.parse_args()
    manager = SimulationManager(ROOT / "data" / "simulation_runs")
    run_dir = manager.prepare(build_definition(args.run_id, wind_mode=args.wind_mode, speed_mps=args.speed_mps))
    result: dict[str, object] = {"generated": True, "run_dir": str(run_dir), "state": manager.state}
    if args.live:
        result.update(run_live(manager, run_dir, args.timeout_s, short_route=args.short_route))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
