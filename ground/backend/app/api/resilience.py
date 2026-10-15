"""Stage 8S Engineering fault and locked operator-control endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from rescue_core.resilience.models import FailureType, OperatorAction

router = APIRouter(prefix="/api", tags=["resilience"])


class FailureRequest(BaseModel):
    mission_id: str
    failure_type: FailureType
    phase: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class OperatorRequest(BaseModel):
    mission_state: str


def _manager(request: Request):
    return request.app.state.resilience_manager


@router.get("/system/resilience")
def status(request: Request, mission_id: str | None = None) -> object:
    return _manager(request).status(mission_id)


@router.get("/engineering/failures/scenarios")
def scenarios(request: Request) -> object:
    manager = _manager(request)
    try:
        manager.ensure_injection_enabled()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [item.model_dump(mode="json") for item in manager.scenarios.values()]


@router.post("/engineering/failures/inject")
def inject(payload: FailureRequest, request: Request) -> object:
    try:
        return _manager(request).inject(payload.mission_id, payload.failure_type, payload.phase, payload.parameters)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/engineering/failures/scenarios/{scenario_id}/run")
def run_scenario(scenario_id: str, request: Request) -> object:
    try:
        return _manager(request).run_scenario(scenario_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/missions/{mission_id}/recovery/snapshot")
def snapshot(mission_id: str, request: Request) -> object:
    return _manager(request).save_recovery_snapshot(mission_id)


@router.post("/missions/{mission_id}/recovery/reconcile")
def reconcile(mission_id: str, request: Request) -> object:
    return _manager(request).reconcile(mission_id)


@router.post("/operator/missions/{mission_id}/actions/{action}")
def operator_action(mission_id: str, action: OperatorAction, payload: OperatorRequest, request: Request) -> object:
    result = _manager(request).operator_action(mission_id, action, payload.mission_state)
    if not result["allowed"]:
        raise HTTPException(status_code=403, detail=result["rejected_reason"])
    return result
