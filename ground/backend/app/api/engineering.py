"""Simulator-only engineering routes, unavailable in competition mode."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/engineering/simulator", tags=["engineering-simulator"])


def _manager(request: Request):
    config = request.app.state.config
    if config.competition.enabled or not config.simulator.enabled:
        raise HTTPException(status_code=403, detail="Engineering simulator APIs are disabled")
    return request.app.state.simulator_manager


@router.post("/{mission_id}/start")
async def start(mission_id: str, request: Request) -> object:
    return await _manager(request).start(mission_id)


@router.post("/{mission_id}/pause")
def pause(mission_id: str, request: Request) -> object:
    return _manager(request).pause(mission_id)


@router.post("/{mission_id}/resume")
def resume(mission_id: str, request: Request) -> object:
    return _manager(request).resume(mission_id)


@router.post("/{mission_id}/reset")
async def reset(mission_id: str, request: Request) -> object:
    return await _manager(request).reset(mission_id)


@router.post("/{mission_id}/inject-target")
async def inject_target(mission_id: str, request: Request) -> object:
    return await _manager(request).inject_target(mission_id)


@router.post("/{mission_id}/confirm-target")
async def confirm_target(mission_id: str, request: Request) -> object:
    return await _manager(request).confirm_target(mission_id)


@router.post("/{mission_id}/simulate-delivery")
async def delivery(mission_id: str, request: Request) -> object:
    return await _manager(request).simulate_delivery(mission_id)


@router.post("/{mission_id}/low-battery")
def low_battery(mission_id: str, request: Request) -> object:
    _manager(request).source(mission_id).low_battery = True
    return {"status": "injected", "condition": "low_battery"}


@router.post("/{mission_id}/gps-degradation")
def gps_degradation(mission_id: str, request: Request) -> object:
    _manager(request).source(mission_id).gps_degraded = True
    return {"status": "injected", "condition": "gps_degradation"}


@router.post("/{mission_id}/link-loss")
def link_loss(mission_id: str, request: Request) -> object:
    _manager(request).source(mission_id).link_lost = True
    return {"status": "injected", "condition": "link_loss"}


@router.post("/{mission_id}/complete")
async def complete(mission_id: str, request: Request) -> object:
    return await _manager(request).complete(mission_id)

