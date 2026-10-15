"""Opt-in Phase 2C acceptance with real SITL/Gazebo and two human actors.

No observations or vehicle state transitions are injected. The tests only stop
fleet sessions they start; they never kill unrelated processes.
"""

import json
import os
import socket
import time
import asyncio
import math
from datetime import UTC, datetime
from uuid import uuid4
from urllib.request import Request, urlopen

import pytest

from ground.backend.tests import test_multidrone_phase2b_genuine_live as baseline
from ground.backend.app.multidrone.contracts import (
    DroneInstance, DroneRole, FleetConfiguration, PrioritySource, ServicePriority,
)
from ground.backend.app.multidrone.runtime import MultiDroneRuntime


def _request(path: str, payload: dict | None = None) -> dict:
    base = os.getenv("RESCUESWARM_PHASE2C_BACKEND", "http://127.0.0.1:8000")
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(base + path, data=data,
                      headers={"Content-Type": "application/json"} if data else {},
                      method="POST" if data else "GET")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _competing_single_run() -> dict | None:
    """Read-only guard for the separately hosted Simulation Lab lifecycle."""
    try:
        with urlopen("http://127.0.0.1:8000/api/simulation/status", timeout=1) as response:
            status = json.load(response)
    except (OSError, ValueError):
        return None
    return status if status.get("state") in {"STARTING", "RUNNING", "STOPPING"} else None


@pytest.mark.sitl_fleet
@pytest.mark.skipif(not os.getenv("RESCUESWARM_PHASE2C_MISSION_ID"),
                    reason="Set RESCUESWARM_PHASE2C_MISSION_ID to a validated prepared two-person Gazebo mission")
def test_phase2c_two_real_survivors_two_verified_service_cycles():
    mission_id = os.environ["RESCUESWARM_PHASE2C_MISSION_ID"]
    before = _request("/api/multidrone/status")
    assert before["state"] in {"IDLE", "STOPPED", "ERROR"}, "Do not replace an active fleet"
    config = {
        "mission_id": mission_id,
        "disable_servicer_camera": True,
        "drones": [
            {"vehicle_id": "drone_01", "role": "SEARCHER", "system_id": 1,
             "instance_id": 0, "spawn_north_m": 0},
            {"vehicle_id": "drone_02", "role": "SERVICER", "system_id": 2,
             "instance_id": 1, "spawn_north_m": 2},
        ],
    }
    started = _request("/api/multidrone/start", config)
    session_id = started["session"]["session_id"]
    seen = {"camera_frames": 0, "inferences": 0, "search_progress": 0.0,
            "canonical_ids": set(), "camera_confirmed_ids": set(),
            "releases": set(), "verified": set()}
    deadline = time.monotonic() + 45 * 60
    try:
        while time.monotonic() < deadline:
            status = _request("/api/multidrone/status")
            assert status["session"]["session_id"] == session_id
            vehicles = {v["vehicle_id"]: v for v in status["vehicles"]}
            searcher = vehicles["drone_01"]
            seen["camera_frames"] = max(seen["camera_frames"], searcher.get("camera_frame_count", 0))
            seen["inferences"] = max(seen["inferences"], searcher.get("camera_inference_count", 0))
            seen["search_progress"] = max(seen["search_progress"], searcher.get("progress_percent", 0))
            seen["canonical_ids"].update(t["canonical_id"] for t in status["targets"])
            seen["camera_confirmed_ids"].update(t["canonical_id"] for t in status["targets"]
                                                if "drone_01" in t.get("source_drone_ids", []))
            seen["releases"].update(a["release_id"] for a in status["assignments"] if a.get("release_id"))
            seen["verified"].update(a["canonical_survivor_id"] for a in status["assignments"]
                                    if a.get("delivery_verified") and a.get("state") == "COMPLETED")
            if len(seen["verified"]) >= 2:
                break
            if status["state"] in {"ERROR", "STOPPED", "COMPLETED"}:
                pytest.fail(f"Fleet ended before two verified service cycles: {status['state']}; {seen}")
            time.sleep(2)
        assert seen["camera_frames"] > 0 and seen["inferences"] > 0, seen
        assert seen["search_progress"] > 0, seen
        assert len(seen["canonical_ids"]) == 2, seen
        assert seen["camera_confirmed_ids"] == seen["canonical_ids"], seen
        assert len(seen["releases"]) == len(seen["verified"]) == 2, seen
    finally:
        current = _request("/api/multidrone/status")
        if (current.get("session") or {}).get("session_id") == session_id and current["state"] not in {"STOPPED", "ERROR"}:
            _request("/api/multidrone/stop", {})
            end = time.monotonic() + 10 * 60
            while time.monotonic() < end:
                if _request("/api/multidrone/status")["state"] == "STOPPED":
                    break
                time.sleep(2)
            else:
                pytest.fail("Owned fleet did not finish safe stop/cleanup within ten minutes")
        final = _request("/api/multidrone/status")
        if (final.get("session") or {}).get("session_id") == session_id and final["state"] == "STOPPED":
            disarmed = {e.get("vehicle_id") for e in final.get("events", [])
                        if e.get("type") == "VEHICLE_DISARMED"}
            assert disarmed == {"drone_01", "drone_02"}, disarmed
            for port in (45678, 5760, 5770):
                with socket.socket() as probe:
                    probe.settimeout(0.3)
                    assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Fleet port {port} still owned"


async def _run_isolated_genuine_acceptance(tmp_path):
    """Build a small real two-actor world; never inject perception/telemetry."""
    root = baseline.REPOSITORY_ROOT
    settings = baseline.load_ground_config(root / "config" / "ground.yaml")
    db = baseline.Database(f"sqlite:///{tmp_path / 'phase2c_acceptance.db'}")
    db.create_schema()
    missions_repo = baseline.SqlAlchemyMissionRepository(db.session_factory)
    vehicles_repo = baseline.SqlAlchemyVehicleRepository(db.session_factory)
    targets_repo = baseline.SqlAlchemyTargetRepository(db.session_factory)
    events_repo = baseline.SqlAlchemyEventRepository(db.session_factory)
    plans_repo = baseline.SqlAlchemyPlanRepository(db.session_factory)
    executions_repo = baseline.SqlAlchemyExecutionRepository(db.session_factory)
    missions = baseline.MissionService(missions_repo, events_repo, vehicles_repo, targets_repo)
    websocket = baseline.WebSocketManager()
    geolocation = baseline.GeolocationMissionService(targets_repo, events_repo, websocket)
    planning = baseline.PlanningService(missions_repo, plans_repo, events_repo)
    sitl = baseline.SITLManager(settings, root, missions, plans_repo, executions_repo,
                                vehicles_repo, events_repo, websocket, geolocation)
    lab = baseline.SimulationLabManager(root / "data" / "simulation_runs")
    runtime = MultiDroneRuntime(root, settings, missions, planning, lab, sitl, websocket)

    origin = baseline.GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584.0)
    frame = baseline.LocalCoordinateFrame(origin)
    polygon = [(5.0, 5.0), (65.0, 5.0), (65.0, 45.0), (5.0, 45.0)]
    boundary_geo = [frame.to_wgs84(baseline.EnuPoint(e, n, 0.0)) for e, n in polygon]
    area = baseline.Polygon(polygon).area
    boundary = baseline.Boundary(vertices=boundary_geo, source_format="geojson",
                                 area_m2=area, area_hectares=area / 10000.0, origin=origin)
    mission = missions.create("Phase 2C genuine two-person acceptance")
    missions.attach_boundary(mission.id, boundary)
    missions.validate(mission.id)
    plan = planning.generate(mission.id, baseline.CoverageRequest(
        search_altitude_m=20.0, lane_spacing_m=12.0, effective_swath_m=12.0,
        edge_margin_m=3.0, search_speed_m_s=4.0, turn_penalty_m=3.0,
        min_planned_coverage_percent=90.0,
        sweep_strategy=baseline.SweepStrategy.FIXED_ANGLE, fixed_sweep_angle_deg=0,
    ))
    assert plan.validation.valid

    truth_enu = [(25.0, 10.0), (40.0, 10.0)]
    placements = []
    for index, (east, north) in enumerate(truth_enu, start=1):
        geo = frame.to_wgs84(baseline.EnuPoint(east, north, 0.0))
        placements.append(baseline.SurvivorPlacement(
            survivor_id=f"phase2c_person_{index:02d}", latitude=geo.latitude,
            longitude=geo.longitude, up_m=0.0, yaw_deg=0.0, body_state="standing",
            visual_variant="CESIUM_MAN_CC_BY_4_0", enabled=True,
        ))
    scenario_id = f"phase2c_genuine_{uuid4().hex[:8]}"
    survivors = baseline.SurvivorScenario(scenario_id=scenario_id, survivors=placements)
    drone = baseline.load_drone_profile(root / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
    camera = baseline.load_camera_profile(root / "simulation" / "profiles" / "ov9281_acceptance_simulation.yaml")
    environment = baseline.load_environment_profile(root / "simulation" / "profiles" / "perception_moderate_environment.yaml")
    world = baseline.MissionWorldProfile(
        mission_id=mission.id,
        boundary_wgs84=[(p.latitude, p.longitude) for p in boundary_geo],
        origin_wgs84=(origin.latitude, origin.longitude, origin.altitude_m),
        ground_elevation_m={"value": 0, "unit": "m", "provenance": "SIMULATION_ONLY"},
        world_dimensions_m={"value": [200, 200, 80], "unit": "m", "provenance": "SIMULATION_ONLY"},
        launch_zone_width_m={"value": 5, "unit": "m", "provenance": "SIMULATION_ONLY"},
        launch_zone_length_m={"value": 5, "unit": "m", "provenance": "SIMULATION_ONLY"},
        environment_profile_id=environment.profile_id,
    )
    config = baseline.SimulationConfig(
        config_id=f"phase2c_{uuid4().hex[:8]}", drone_profile_id=drone.profile_id,
        camera_profile_id=camera.camera_id, environment_profile_id=environment.profile_id,
        mission_world_profile_id=mission.id, survivor_scenario_id=scenario_id, random_seed=42,
    )
    definition = baseline.SimulationRunDefinition(
        run_id=f"phase2c_template_{uuid4().hex[:8]}", config=config, drone=drone,
        camera=camera, environment=environment, mission=world, survivors=survivors,
        fleet_count=2, fleet_spawn_offset_m=2.0, disable_servicer_camera=True,
    )
    competing = _competing_single_run()
    if competing:
        pytest.fail(f"Single-drone Simulation Lab already owns resources: {competing.get('run_id')}")
    lab.prepare(definition)
    fleet = FleetConfiguration(mission_id=mission.id, disable_servicer_camera=True,
        drones=[DroneInstance(vehicle_id="drone_01", role=DroneRole.SEARCHER,
                              system_id=1, instance_id=0, spawn_north_m=0),
                DroneInstance(vehicle_id="drone_02", role=DroneRole.SERVICER,
                              system_id=2, instance_id=1, spawn_north_m=2)])
    started = await runtime.start(fleet)
    session_id = started["session"]["session_id"]
    evidence = {"mission_id": mission.id, "session_id": session_id,
                "truth_enu_m": truth_enu, "max_camera_frames": 0, "max_inferences": 0,
                "targets": {}, "assignments": {}, "searcher_progress": 0.0,
                "priority_change": None, "events": [], "final_state": None,
                "dispatch_trace": [], "concurrent_simulation": None,
                "fleet_error": None}
    try:
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            status = runtime.status()
            vehicle = next(v for v in status["vehicles"] if v["vehicle_id"] == "drone_01")
            evidence["max_camera_frames"] = max(evidence["max_camera_frames"], vehicle.get("camera_frame_count", 0))
            evidence["max_inferences"] = max(evidence["max_inferences"], vehicle.get("camera_inference_count", 0))
            evidence["searcher_progress"] = max(evidence["searcher_progress"], vehicle.get("progress_percent", 0))
            evidence["targets"].update({t["canonical_id"]: t for t in status["targets"]})
            evidence["assignments"].update({a["canonical_survivor_id"]: a for a in status["assignments"]})
            evidence["events"] = status["events"]
            evidence["final_state"] = status["state"]
            evidence["fleet_error"] = status.get("error")
            evidence["concurrent_simulation"] = _competing_single_run()
            if evidence["concurrent_simulation"]:
                break
            servicer = next(v for v in status["vehicles"] if v["vehicle_id"] == "drone_02")
            waiting = next((a for a in status["assignments"] if a["state"] == "QUEUED"), None)
            sample = {
                "at": datetime.now(UTC).isoformat(),
                "state": servicer["state"], "connected": servicer.get("connected"),
                "telemetry_age_s": servicer.get("telemetry_age_s"),
                "mode": (servicer.get("telemetry") or {}).get("flight_mode"),
                "armed": (servicer.get("telemetry") or {}).get("armed"),
                "current_item": servicer.get("current_mission_item"),
                "queued_reason": waiting["reason"] if waiting else None,
            }
            if not evidence["dispatch_trace"] or any(
                sample[key] != evidence["dispatch_trace"][-1][key]
                for key in ("state", "connected", "mode", "armed", "current_item", "queued_reason")
            ):
                evidence["dispatch_trace"].append(sample)
            if len(status["targets"]) >= 2 and evidence["priority_change"] is None:
                # Explicit operator action on a confirmed canonical ID; no truth enters dispatch.
                second_id = status["targets"][1]["canonical_id"]
                updated = runtime.coordinator.set_priority(second_id, ServicePriority.CRITICAL, PrioritySource.OPERATOR)
                evidence["priority_change"] = {"canonical_id": second_id,
                                               "priority": updated.priority.value,
                                               "source": updated.priority_source.value}
            completed = [a for a in status["assignments"]
                         if a.get("state") == "COMPLETED" and a.get("delivery_verified")]
            if len(completed) >= 2:
                break
            if status["state"] in {"ERROR", "STOPPED", "COMPLETED"}:
                break
        evidence["estimated_enu_m"] = {cid: [t.get("east_m"), t.get("north_m")]
                                       for cid, t in evidence["targets"].items()}
        # Ground truth is read only after runtime estimates are captured.
        estimates = [p for p in evidence["estimated_enu_m"].values() if None not in p]
        if len(estimates) == 2:
            direct = [math.dist(estimates[i], truth_enu[i]) for i in range(2)]
            swapped = [math.dist(estimates[i], truth_enu[1-i]) for i in range(2)]
            evidence["evaluation_error_m"] = min((direct, swapped), key=sum)
    finally:
        if runtime.task and not runtime.task.done():
            await runtime.stop()
            try:
                await asyncio.wait_for(runtime.task, timeout=10 * 60)
            except asyncio.TimeoutError:
                evidence["cleanup_error"] = "Safe fleet return did not complete within ten minutes"
        await runtime.shutdown()
        stopped = runtime.status()
        evidence["stopped_state"] = stopped["state"]
        evidence["stop_events"] = [event for event in stopped["events"]
                                   if event["type"] in {"RETURN_REQUESTED", "RTL_STARTED",
                                                        "VEHICLE_LANDED", "VEHICLE_DISARMED",
                                                        "MISSION_COMPLETED", "FLEET_STOPPED"}]
        evidence["final_vehicle_states"] = {vehicle["vehicle_id"]: vehicle["state"]
                                            for vehicle in stopped["vehicles"]}
        evidence["released_tcp_ports"] = {}
        for port in (45678, 45679, 5760, 5770):
            with socket.socket() as probe:
                probe.settimeout(0.3)
                evidence["released_tcp_ports"][port] = probe.connect_ex(("127.0.0.1", port)) != 0
        print("PHASE2C_GENUINE_EVIDENCE=" + json.dumps(evidence, default=str), flush=True)
    assert evidence["concurrent_simulation"] is None, (
        f"Another Simulation Lab run started during acceptance: {evidence['concurrent_simulation']}"
    )
    assert evidence["max_camera_frames"] > 0 and evidence["max_inferences"] > 0
    assert len(evidence["targets"]) == 2
    assert evidence["priority_change"] is not None
    assert len([a for a in evidence["assignments"].values()
                if a.get("state") == "COMPLETED" and a.get("delivery_verified")]) == 2
    assert len({a.get("release_id") for a in evidence["assignments"].values()}) == 2
    assert evidence["searcher_progress"] > 0
    assert evidence["stopped_state"] == "STOPPED"
    assert {event["vehicle_id"] for event in evidence["stop_events"]
            if event["type"] == "VEHICLE_DISARMED"} == {"drone_01", "drone_02"}
    assert all(evidence["released_tcp_ports"].values())
    return evidence


@pytest.mark.sitl_fleet
@pytest.mark.skipif(os.getenv("RUN_GENUINE_PHASE2C_ISOLATED") != "1",
                    reason="Set RUN_GENUINE_PHASE2C_ISOLATED=1 for isolated real SITL/Gazebo acceptance")
def test_phase2c_isolated_two_person_real_fleet(tmp_path):
    asyncio.run(_run_isolated_genuine_acceptance(tmp_path))
