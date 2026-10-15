"""Coverage planning orchestration and durable reproducibility metadata."""

from __future__ import annotations

import hashlib
import json

from ..domain.events import EventType, MissionEvent
from ..domain.mission import MissionState
from ..planning.coverage_planner import CoveragePlanner
from ..planning.models import CoveragePlan, CoverageRequest
from ..repositories.contracts import EventRepository, MissionRepository, PlanRepository
from .mission_service import MissionNotFoundError


def coverage_plan_fingerprint(plan: CoveragePlan) -> str:
    payload = plan.model_dump(
        mode="json",
        exclude={"created_at", "plan_fingerprint", "mission_fingerprint"},
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class PlanningService:
    def __init__(
        self,
        missions: MissionRepository,
        plans: PlanRepository,
        events: EventRepository,
        planner: CoveragePlanner | None = None,
    ):
        self.missions = missions
        self.plans = plans
        self.events = events
        self.planner = planner or CoveragePlanner()

    def generate(self, mission_id: str, request: CoverageRequest) -> CoveragePlan:
        mission = self.missions.get(mission_id)
        if mission is None:
            raise MissionNotFoundError(mission_id)
        if mission.boundary is None or mission.state not in {MissionState.VALIDATED, MissionState.READY}:
            raise ValueError("Mission boundary must be validated before coverage planning")
        if request.flight_parameters is None and getattr(mission, "flight_parameters", None) is not None:
            request.flight_parameters = mission.flight_parameters
        plan = self.planner.plan(mission_id, mission.boundary, request)
        plan.plan_fingerprint = coverage_plan_fingerprint(plan)
        self.plans.upsert(plan)
        mission.state = MissionState.READY
        self.missions.update(mission)
        self.events.add(
            MissionEvent(
                mission_id=mission_id,
                type=EventType.COVERAGE_PLAN_CREATED,
                message=f"Validated coverage plan created ({plan.estimated_path_length_m:.0f} m)",
                metadata={
                    "plan_fingerprint": plan.plan_fingerprint,
                    "sweep_angle_deg": plan.sweep_angle_deg,
                    "planned_coverage_percent": plan.validation.planned_coverage_percent,
                },
            )
        )
        return plan

    def get(self, mission_id: str) -> CoveragePlan | None:
        if self.missions.get(mission_id) is None:
            raise MissionNotFoundError(mission_id)
        return self.plans.get(mission_id)
