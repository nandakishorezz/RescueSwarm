"""Stage 13SIM laboratory API with an explicit ground-truth firewall."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import EnuPoint
from rescue_core.simulation.georef import SimulationGeoreference
from rescue_core.simulation.orchestration import SimulationRunDefinition
from rescue_core.simulation.profiles import load_camera_profile, load_drone_profile, load_environment_profile
from rescue_core.simulation.sensor_realism import sensor_realism_profiles

router = APIRouter(prefix="/api/simulation", tags=["simulation-lab"])


class GeoreferenceRequest(BaseModel):
    origin_latitude: float
    origin_longitude: float
    origin_altitude_m: float = 0
    east_m: float
    north_m: float
    up_m: float = 0


def _manager(request: Request):
    if request.app.state.config.competition.enabled:
        raise HTTPException(status_code=403, detail="Simulation Lab is disabled in Competition Mode")
    return request.app.state.simulation_lab_manager


def _latest_perception_run(manager) -> Path:
    candidates = [
        path for path in manager.runs_root.iterdir()
        if path.is_dir() and (
            (path / "vision_status.json").is_file()
            or (path / "phase2_run_summary.json").is_file()
        )
    ] if manager.runs_root.is_dir() else []
    if not candidates:
        raise HTTPException(status_code=404, detail="No pixel-perception run is available")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


@router.get("/status")
def status(request: Request) -> object:
    """Deliberately excludes every ground-truth coordinate."""
    return _manager(request).status(include_truth=False)


@router.get("/profiles/reference")
def reference_profiles(request: Request) -> object:
    _manager(request)
    root: Path = request.app.state.repository_root
    drone = load_drone_profile(root / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
    camera = load_camera_profile(root / "simulation" / "profiles" / "ov9281_reference_simulation.yaml")
    environment = load_environment_profile(root / "simulation" / "profiles" / "moderate_gust_environment.yaml")
    return {
        "drone": drone.model_dump(mode="json"),
        "camera": camera.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "sensor_profiles": {
            key.value: value.model_dump(mode="json")
            for key, value in sensor_realism_profiles().items()
        },
    }


@router.post("/georeference/enu-to-world")
def enu_to_world(payload: GeoreferenceRequest, request: Request) -> object:
    _manager(request)
    origin = GeoPoint(
        latitude=payload.origin_latitude,
        longitude=payload.origin_longitude,
        altitude_m=payload.origin_altitude_m,
    )
    frame = SimulationGeoreference(origin)
    enu = EnuPoint(east_m=payload.east_m, north_m=payload.north_m, up_m=payload.up_m)
    point = frame.local.to_wgs84(enu)
    gazebo = frame.enu_to_gazebo(enu)
    return {
        "wgs84": {"latitude": point.latitude, "longitude": point.longitude, "altitude_m": point.altitude_m},
        "enu_m": [payload.east_m, payload.north_m, payload.up_m],
        "gazebo_m": [gazebo.x_m, gazebo.y_m, gazebo.z_m],
        "axis_contract": "Mission ENU E,N,U equals Gazebo X,Y,Z",
    }


@router.post("/prepare")
def prepare(definition: SimulationRunDefinition, request: Request) -> object:
    manager = _manager(request)
    try:
        run_dir = manager.prepare(definition)
    except (RuntimeError, ValueError, FileExistsError) as exc:
        raise HTTPException(status_code=409 if isinstance(exc, (RuntimeError, FileExistsError)) else 422, detail=str(exc)) from exc
    result = manager.status(include_truth=False)
    result["artifact_run_id"] = run_dir.name
    return result


@router.post("/start")
def start(request: Request) -> object:
    """Kick off the blocking Gazebo/SITL startup in a background thread.
    Returns immediately with state=STARTING_GAZEBO so the browser never
    times out. Poll /api/simulation/status until state==RUNNING."""
    manager = _manager(request)
    fleet = request.app.state.multidrone_runtime
    if fleet.task is not None and not fleet.task.done():
        raise HTTPException(status_code=409, detail="Multi-drone fleet owns the active Gazebo/SITL world")
    if manager.state not in ("READY",):
        raise HTTPException(
            status_code=409,
            detail=f"cannot start while simulation is {manager.state}",
        )

    def _run() -> None:
        try:
            manager.start()
        except Exception:
            pass  # error is captured in manager.error and state=FAILED

    thread = threading.Thread(target=_run, daemon=True, name="sim-start")
    thread.start()
    # Return the current status immediately (state will be STARTING_GAZEBO)
    return manager.status(include_truth=False)


@router.post("/stop")
async def stop(request: Request) -> object:
    manager = _manager(request)
    # The Lab owns Gazebo/SITL; the active Mission execution owns the camera
    # bridge.  Stop both explicitly so a fresh Lab run cannot receive frames
    # from an earlier mission's WSL helper.
    await request.app.state.sitl_manager.stop_surveillance()
    manager.stop()
    return manager.status(include_truth=False)


@router.get("/observer/truth")
def observer_truth(request: Request, observer_ground_truth: bool = False) -> object:
    """Only this opt-in observer namespace may cross the truth firewall."""
    if not observer_ground_truth:
        raise HTTPException(status_code=403, detail="Explicit observer_ground_truth=true acknowledgement required")
    return _manager(request).status(include_truth=True)


@router.get("/perception/latest")
def perception_latest(request: Request) -> object:
    """Autonomy-side camera and detector status; never returns truth."""
    run = _latest_perception_run(_manager(request))
    result: dict[str, object] = {"run_id": run.name, "classification": "AUTONOMY_CAMERA_ONLY"}
    for name, key in (("vision_status.json", "vision"), ("perception_monitor/latest_camera_frame.json", "frame")):
        path = run / name
        if path.is_file():
            result[key] = json.loads(path.read_text(encoding="utf-8"))
    summary_path = run / "phase2_run_summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        result["result"] = {
            key: summary.get(key) for key in (
                "proof_source", "raw_person_detections", "geolocated_observations",
                "confirmed_targets", "frames_inferred", "detector_fps_wall",
                "inference_p50_ms", "inference_p95_ms", "renderer", "passed",
            )
        }
    return result


@router.get("/perception/latest/frame")
def perception_latest_frame(request: Request, overlay: bool = True) -> FileResponse:
    run = _latest_perception_run(_manager(request))
    name = "latest_AUTONOMY_OUTPUT.png" if overlay else "latest_AUTONOMY_INPUT_MONO8.png"
    path = run / "perception_monitor" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No virtual-camera monitor frame is available yet")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/observer/perception/latest")
def observer_perception_latest(request: Request, observer_ground_truth: bool = False) -> object:
    if not observer_ground_truth:
        raise HTTPException(status_code=403, detail="Explicit observer_ground_truth=true acknowledgement required")
    run = _latest_perception_run(_manager(request))
    summary = run / "phase2_run_summary.json"
    evidence = run / "PIXEL_ONLY_PERCEPTION_EVIDENCE.json"
    return {
        "run_id": run.name,
        "classification": "GROUND_TRUTH_SIMULATION_ONLY",
        "summary": json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None,
        "prediction_vs_truth": json.loads(evidence.read_text(encoding="utf-8")) if evidence.is_file() else None,
    }


@router.get("/artifacts/{run_id}/summary")
def artifact_summary(run_id: str, request: Request) -> object:
    manager = _manager(request)
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid run ID")
    path = (manager.runs_root / run_id / "run_summary.json").resolve()
    if manager.runs_root.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Simulation summary not found")
    return json.loads(path.read_text(encoding="utf-8"))
