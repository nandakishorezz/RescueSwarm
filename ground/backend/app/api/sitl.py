"""Engineering-only ArduCopter SITL process and mission routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from rescue_core.simulation.orchestration import SimulationState
from pydantic import BaseModel, Field

from rescue_core.autonomy.models import SyntheticSurvivorScenario

router = APIRouter(prefix="/api/engineering/sitl", tags=["engineering-sitl"])


class SITLStartRequest(BaseModel):
    mission_id: str
    launch_altitude_m: float
    heading_deg: float = Field(default=0, ge=0, lt=360)


class RescueRunRequest(BaseModel):
    target_id: str = "synthetic_survivor_01"
    true_east_m: float
    true_north_m: float
    visibility_distance_m: float = Field(default=40, gt=0)
    detection_probability: float = Field(default=1, ge=0, le=1)
    pixel_noise_stddev: float = Field(default=0, ge=0)
    coarse_estimate_offset_east_m: float = 0
    coarse_estimate_offset_north_m: float = 0
    delivery_required: bool = True
    seed: int = 7
    auto_start_sitl: bool = True


def _manager(request: Request):
    manager = request.app.state.sitl_manager
    try:
        manager.ensure_enabled()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return manager


def _require_no_fleet(request: Request) -> None:
    fleet = request.app.state.multidrone_runtime
    if fleet.task is not None and not fleet.task.done():
        raise HTTPException(status_code=409, detail="Multi-drone fleet owns the active Gazebo/SITL world")


@router.post("/start")
def start(payload: SITLStartRequest, request: Request) -> object:
    _require_no_fleet(request)
    return _manager(request).start_process(payload.mission_id, payload.launch_altitude_m, payload.heading_deg)


@router.post("/stop")
def stop(request: Request) -> object:
    return _manager(request).stop_process()


@router.get("/status")
def status(request: Request, mission_id: str | None = None) -> object:
    return _manager(request).status(mission_id)


@router.post("/missions/{mission_id}/execute")
async def execute(mission_id: str, request: Request) -> object:
    _require_no_fleet(request)
    manager = _manager(request)
    lab = request.app.state.simulation_lab_manager
    if (
        lab.definition is None
        or lab.definition.mission.mission_id != mission_id
        or lab.state in {SimulationState.COMPLETED, SimulationState.FAILED}
    ):
        # The Lab is intentionally process-local, but its generated definition
        # is durable. A backend restart or completed run must not turn the
        # browser's current prepared mission into a generic SITL connection failure.
        if not lab.restore_prepared_run(mission_id):
            try:
                from rescue_core.simulation.profiles import (
                    load_drone_profile,
                    load_camera_profile,
                    load_environment_profile,
                    MissionWorldProfile,
                    SimulationConfig,
                    SurvivorScenario,
                )
                from rescue_core.simulation.sensor_realism import sensor_realism_profiles, SensorSeeds
                from rescue_core.simulation.orchestration import SimulationRunDefinition
                import time

                mission = request.app.state.mission_service.get_mission(mission_id)
                if mission and mission.boundary and len(mission.boundary.vertices) >= 3:
                    root = request.app.state.repository_root
                    drone = load_drone_profile(root / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
                    camera = load_camera_profile(root / "simulation" / "profiles" / "ov9281_reference_simulation.yaml")
                    environment = load_environment_profile(root / "simulation" / "profiles" / "moderate_gust_environment.yaml")
                    sensor_profile = list(sensor_realism_profiles().values())[0]

                    origin_lat = mission.boundary.origin.latitude if mission.boundary.origin else mission.boundary.vertices[0].latitude
                    origin_lon = mission.boundary.origin.longitude if mission.boundary.origin else mission.boundary.vertices[0].longitude
                    poly_wgs = [(v.latitude, v.longitude) for v in mission.boundary.vertices]

                    survivors = [
                        {
                            "id": "survivor_01",
                            "survivor_id": "survivor_01",
                            "latitude": 12.97297408,
                            "longitude": 77.59207073,
                            "up_m": 0.0,
                            "body_state": "standing",
                            "enabled": True,
                        },
                        {
                            "id": "survivor_02",
                            "survivor_id": "survivor_02",
                            "latitude": 12.97291984,
                            "longitude": 77.59239332,
                            "up_m": 0.0,
                            "body_state": "standing",
                            "enabled": True,
                        },
                        {
                            "id": "survivor_03",
                            "survivor_id": "survivor_03",
                            "latitude": 12.97306447,
                            "longitude": 77.59223664,
                            "up_m": 0.0,
                            "body_state": "standing",
                            "enabled": True,
                        },
                    ]

                    run_id = f"stage13sim_autoprep_{int(time.time() * 1000)}"
                    definition = SimulationRunDefinition(
                        run_id=run_id,
                        config=SimulationConfig.model_validate({
                            "schema_version": "1.0",
                            "config_id": f"{run_id}_config",
                            "drone_profile_id": drone.profile_id,
                            "camera_profile_id": camera.camera_id,
                            "environment_profile_id": environment.profile_id,
                            "mission_world_profile_id": mission_id,
                            "survivor_scenario_id": f"{run_id}_hidden",
                            "random_seed": int(environment.random_seed or 84521),
                            "execution_source": "SITL_GAZEBO",
                        }),
                        drone=drone,
                        camera=camera,
                        environment=environment,
                        mission=MissionWorldProfile.model_validate({
                            "schema_version": "1.0",
                            "mission_id": mission_id,
                            "boundary_wgs84": poly_wgs,
                            "origin_wgs84": [origin_lat, origin_lon, 0.0],
                            "ground_elevation_m": {"value": 0.0, "unit": "m", "provenance": "SIMULATION_ONLY"},
                            "world_dimensions_m": {"value": [4000.0, 4000.0, 80.0], "unit": "m", "provenance": "ENGINEERING_ESTIMATE"},
                            "launch_zone_width_m": {"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
                            "launch_zone_length_m": {"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
                            "terrain_mode": "FLAT",
                            "lighting": "DAY",
                            "environment_profile_id": environment.profile_id,
                        }),
                        survivors=SurvivorScenario.model_validate({
                            "schema_version": "1.0",
                            "scenario_id": f"{run_id}_hidden",
                            "survivors": survivors,
                        }),
                        sensor_profile=sensor_profile,
                        sensor_seeds=SensorSeeds.model_validate({
                            "sensor_seed": 31003,
                            "wind_seed": int(environment.wind.random_seed if environment.wind else 84521),
                            "scenario_seed": int(environment.random_seed or 84521),
                        }),
                        fleet_count=1,
                    )
                    lab.prepare(definition)
            except Exception:
                pass

        if lab.state is not SimulationState.READY:
            raise HTTPException(
                status_code=409,
                detail="Prepare the Simulation Lab world before starting the Mission simulation",
            )
    if lab.state is SimulationState.READY:
        # SimulationPage already generated the mission's world.  Start that
        # owned Gazebo/SITL pair here instead of starting legacy `-f quad`.
        await asyncio.to_thread(lab.start)
    if lab.state is not SimulationState.RUNNING or not lab.processes:
        raise HTTPException(
            status_code=409,
            detail=lab.error or "Simulation processes failed to reach RUNNING state",
        )
    if lab.processes and lab.adapter:
        lab.adapter.disconnect()
        lab.adapter = None
    # Use the launcher's actual connection string (resolves to tcp:127.0.0.1:5761 on Windows
    # via socat bridge from WSL port 5760).
    connection = lab.launcher.connection if lab.processes else None
    try:
        return await manager.execute(
            mission_id, connection_override=connection, simulation_owner=lab,
        )
    except Exception:
        # The Lab owns every child it created; do not leave it running when
        # handoff to the normal Mission runner fails.
        await asyncio.to_thread(lab.stop)
        raise


@router.post("/missions/{mission_id}/abort")
def abort(mission_id: str, request: Request) -> object:
    return _manager(request).abort(mission_id)


@router.post("/missions/{mission_id}/rescue/execute")
async def execute_rescue(mission_id: str, payload: RescueRunRequest, request: Request) -> object:
    _require_no_fleet(request)
    manager = _manager(request)
    lab = request.app.state.simulation_lab_manager
    if lab.processes and lab.adapter:
        lab.adapter.disconnect()
        lab.adapter = None
    connection = lab.launcher.connection if lab.processes else None
    scenario = SyntheticSurvivorScenario.model_validate(
        payload.model_dump(exclude={"auto_start_sitl"})
    )
    return await manager.execute_rescue(
        mission_id, scenario, auto_start_sitl=payload.auto_start_sitl, connection_override=connection,
    )
