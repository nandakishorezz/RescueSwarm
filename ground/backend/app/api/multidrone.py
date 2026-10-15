"""Explicit opt-in fleet session; no route alters the single-drone Mission API."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..multidrone.contracts import FleetConfiguration, PrioritySource, ServicePriority, SurvivorObservation

router = APIRouter(prefix="/api/multidrone", tags=["multi-drone-simulation"])


class PriorityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: ServicePriority


def _runtime(request: Request):
    return request.app.state.multidrone_runtime


@router.get("/status")
def status(request: Request) -> object:
    return _runtime(request).status()


@router.post("/start")
async def start(configuration: FleetConfiguration, request: Request) -> object:
    try:
        return await _runtime(request).start(configuration)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (RuntimeError, ValueError, FileExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
async def stop(request: Request) -> object:
    return await _runtime(request).stop()


@router.post("/vehicles/{vehicle_id}/return")
def return_vehicle(vehicle_id: str, request: Request) -> object:
    try:
        return _runtime(request).stop_vehicle(vehicle_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/vehicles/{vehicle_id}/service")
def service_vehicle(vehicle_id: str, request: Request) -> object:
    runtime = _runtime(request)
    try:
        runtime.authorize_service(vehicle_id)
        return {"status": "AUTHORIZED", "vehicle_id": vehicle_id}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/survivors")
def get_survivors(request: Request) -> object:
    runtime = _runtime(request)
    if getattr(runtime, "registry", None) is None:
        return []
    return [r.model_dump(mode="json") for r in runtime.registry.confirmed_survivors()]


@router.post("/survivors/{canonical_id}/priority")
def change_priority(canonical_id: str, change: PriorityChange, request: Request) -> object:
    runtime = _runtime(request)
    if (getattr(runtime, "session", None) is None or
            runtime.session.state not in {"RUNNING", "DEGRADED"} or
            getattr(runtime, "coordinator", None) is None):
        raise HTTPException(status_code=409, detail="No active fleet coordination session")
    try:
        record = runtime.coordinator.set_priority(canonical_id, change.priority, PrioritySource.OPERATOR)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@router.get("/assignments")
def get_assignments(request: Request) -> object:
    runtime = _runtime(request)
    if getattr(runtime, "coordinator", None) is None:
        return []
    return [a.model_dump(mode="json") for a in runtime.coordinator.assignments()]


@router.post("/assignments/{assignment_id}/acknowledge")
def acknowledge_assignment(assignment_id: str, request: Request) -> object:
    runtime = _runtime(request)
    if getattr(runtime, "coordinator", None) is None:
        raise HTTPException(status_code=409, detail="No active fleet coordinator")
    try:
        from uuid import UUID
        aid = UUID(assignment_id)
        return runtime.coordinator.acknowledge_assignment(aid).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/assignments/{assignment_id}/cancel")
def cancel_assignment(assignment_id: str, request: Request) -> object:
    runtime = _runtime(request)
    if getattr(runtime, "coordinator", None) is None:
        raise HTTPException(status_code=409, detail="No active fleet coordinator")
    try:
        from uuid import UUID
        aid = UUID(assignment_id)
        return runtime.coordinator.cancel_assignment(aid).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/observations")
def ingest_observation(observation: SurvivorObservation, request: Request) -> object:
    runtime = _runtime(request)
    if getattr(runtime, "session", None) is None:
        raise HTTPException(status_code=409, detail="No active fleet session")
    if runtime.session.session_id != observation.session_id:
        raise HTTPException(status_code=409, detail="Cross-session observation rejected")
    
    # Validate vehicle_id in fleet
    drones = {d.vehicle_id for d in runtime.session.configuration.drones}
    if observation.vehicle_id not in drones:
        raise HTTPException(status_code=400, detail=f"Vehicle {observation.vehicle_id} not in fleet")

    runtime._observation(observation.session_id, observation.vehicle_id, observation, None)
    return {"status": "INGESTED", "observation_id": observation.observation_id}
