"""Typed access to application-scoped services."""

from fastapi import Request

from ..services.mission_service import MissionService
from ..services.planning_service import PlanningService


def mission_service(request: Request) -> MissionService:
    return request.app.state.mission_service


def planning_service(request: Request) -> PlanningService:
    return request.app.state.planning_service
