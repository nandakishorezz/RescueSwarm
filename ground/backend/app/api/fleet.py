"""One-GCS fleet read model and competition-locked engineering planning."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["fleet"])


class FleetPlanRequest(BaseModel):
    fleet_size: int = Field(default=2, ge=2, le=10)


def _manager(request: Request):
    return request.app.state.fleet_manager


@router.get("/missions/{mission_id}/fleet")
def fleet_status(mission_id: str, request: Request) -> object:
    return _manager(request).status(mission_id)


@router.get("/missions/{mission_id}/fleet/vehicles")
def fleet_vehicles(mission_id: str, request: Request) -> object:
    return _manager(request).status(mission_id).get("vehicles", [])


@router.get("/missions/{mission_id}/fleet/plan")
def fleet_plan(mission_id: str, request: Request) -> object:
    result = _manager(request).get_plan(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Fleet plan not found")
    return result


@router.get("/missions/{mission_id}/fleet/assignments")
def fleet_assignments(mission_id: str, request: Request) -> object:
    return _manager(request).status(mission_id).get("assignments", [])


@router.get("/missions/{mission_id}/fleet/targets")
def fleet_targets(mission_id: str, request: Request) -> object:
    return _manager(request).status(mission_id).get("targets", [])


@router.get("/missions/{mission_id}/fleet/progress")
def fleet_progress(mission_id: str, request: Request) -> object:
    return _manager(request).status(mission_id).get("progress", {})


@router.post("/engineering/fleet/missions/{mission_id}/plan")
def create_fleet_plan(mission_id: str, payload: FleetPlanRequest, request: Request) -> object:
    try:
        return _manager(request).plan(mission_id, payload.fleet_size)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
