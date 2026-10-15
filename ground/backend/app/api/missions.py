"""Mission, boundary, vehicle, target, event, and review APIs."""

from __future__ import annotations

from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

from ..domain.flight_parameters import FlightParameters
from ..services.boundary_service import BoundaryError
from ..services.mission_service import MissionNotFoundError, MissionService
from ..planning.models import CoveragePlan, CoverageRequest
from ..services.planning_service import PlanningService
from .dependencies import mission_service, planning_service

router = APIRouter(prefix="/api/missions", tags=["missions"])


class CreateMissionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _not_found(exc: MissionNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Mission not found: {exc.args[0]}")


@router.get("")
def list_missions(service: MissionService = Depends(mission_service)) -> list[object]:
    return service.missions.list()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_mission(payload: CreateMissionRequest, service: MissionService = Depends(mission_service)) -> object:
    return service.create(payload.name)


@router.get("/{mission_id}")
def get_mission(mission_id: str, service: MissionService = Depends(mission_service)) -> object:
    try:
        return service.require(mission_id)
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/{mission_id}/boundary")
async def import_boundary(
    mission_id: str,
    request: Request,
    boundary_file: UploadFile = File(...),
    service: MissionService = Depends(mission_service),
) -> object:
    filename = PurePath(boundary_file.filename or "").name
    extension = PurePath(filename).suffix.lower()
    config = request.app.state.config.uploads
    if extension not in config.allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported boundary file extension")
    content = await boundary_file.read(config.max_bytes + 1)
    await boundary_file.close()
    if len(content) > config.max_bytes:
        raise HTTPException(status_code=413, detail="Boundary file exceeds configured upload limit")
    try:
        boundary = request.app.state.boundary_service.import_boundary(filename, content)
        return service.attach_boundary(mission_id, boundary)
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc
    except (BoundaryError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{mission_id}/validate")
def validate_mission(mission_id: str, service: MissionService = Depends(mission_service)) -> object:
    try:
        return service.validate(mission_id)
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{mission_id}/vehicles")
def mission_vehicles(mission_id: str, service: MissionService = Depends(mission_service)) -> list[object]:
    service.require(mission_id)
    return service.vehicles.list_for_mission(mission_id)


@router.get("/{mission_id}/telemetry/latest")
def latest_telemetry(mission_id: str, request: Request, service: MissionService = Depends(mission_service)) -> object:
    service.require(mission_id)
    vehicles = service.vehicles.list_for_mission(mission_id)
    v = vehicles[0] if vehicles else None
    exec_record = request.app.state.execution_repository.get(mission_id)
    if not v:
        return {}
    res = v.model_dump(mode="json")
    if exec_record:
        res["current_mission_item"] = exec_record.current_mission_item
        res["total_mission_items"] = exec_record.total_mission_items
    return res



@router.get("/{mission_id}/targets")
def mission_targets(mission_id: str, service: MissionService = Depends(mission_service)) -> list[object]:
    service.require(mission_id)
    return service.targets.list_for_mission(mission_id)


@router.get("/{mission_id}/events")
def mission_events(mission_id: str, service: MissionService = Depends(mission_service)) -> list[object]:
    service.require(mission_id)
    return service.events.list_for_mission(mission_id)


@router.get("/{mission_id}/review")
def mission_review(
    mission_id: str,
    request: Request,
    service: MissionService = Depends(mission_service),
) -> object:
    try:
        review = service.review(mission_id)
        plan = request.app.state.planning_service.get(mission_id)
        execution = request.app.state.execution_repository.get(mission_id)
        return {
            **review,
            "coverage_plan": plan,
            "execution": execution,
            "execution_source": execution.source if execution else "SIMULATED",
        }
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/{mission_id}/plan", response_model=CoveragePlan)
def generate_plan(
    mission_id: str,
    payload: CoverageRequest,
    service: PlanningService = Depends(planning_service),
) -> CoveragePlan:
    try:
        return service.generate(mission_id, payload)
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{mission_id}/plan", response_model=CoveragePlan)
def get_plan(
    mission_id: str,
    service: PlanningService = Depends(planning_service),
) -> CoveragePlan:
    try:
        plan = service.get(mission_id)
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Coverage plan has not been generated")
    return plan


@router.get("/{mission_id}/flight-parameters", response_model=FlightParameters)
def get_flight_parameters(
    mission_id: str,
    service: MissionService = Depends(mission_service),
) -> FlightParameters:
    try:
        mission = service.require(mission_id)
        return getattr(mission, "flight_parameters", None) or FlightParameters()
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc


@router.put("/{mission_id}/flight-parameters", response_model=FlightParameters)
def update_flight_parameters(
    mission_id: str,
    payload: FlightParameters,
    service: MissionService = Depends(mission_service),
) -> FlightParameters:
    try:
        mission = service.require(mission_id)
        if mission.state in {"RUNNING", "RETURNING"}:
            raise HTTPException(
                status_code=409,
                detail="Cannot alter flight setpoints while a mission is actively running or returning",
            )
        mission.flight_parameters = payload
        service.missions.update(mission)
        return mission.flight_parameters
    except MissionNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

