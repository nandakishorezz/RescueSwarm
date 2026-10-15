"""Mission lifecycle orchestration independent of HTTP and database selection."""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.events import EventType, MissionEvent
from ..domain.mission import Boundary, Mission, MissionState
from ..repositories.contracts import EventRepository, MissionRepository, TargetRepository, VehicleRepository


class MissionNotFoundError(KeyError):
    pass


class MissionService:
    def __init__(
        self,
        missions: MissionRepository,
        events: EventRepository,
        vehicles: VehicleRepository,
        targets: TargetRepository,
    ):
        self.missions = missions
        self.events = events
        self.vehicles = vehicles
        self.targets = targets

    def require(self, mission_id: str) -> Mission:
        mission = self.missions.get(mission_id)
        if mission is None:
            raise MissionNotFoundError(mission_id)
        return mission

    def create(self, name: str) -> Mission:
        mission = self.missions.add(Mission(name=name.strip()))
        self.events.add(
            MissionEvent(
                mission_id=mission.id,
                type=EventType.MISSION_CREATED,
                message=f"Mission '{mission.name}' created",
            )
        )
        return mission

    def attach_boundary(self, mission_id: str, boundary: Boundary) -> Mission:
        mission = self.require(mission_id)
        # Allow re-importing a boundary after a completed/failed/aborted run so
        # the operator can retry or start a new search on the same mission record
        # without having to create a brand-new mission.
        _restartable = {
            MissionState.DRAFT,
            MissionState.VALIDATED,
            MissionState.COMPLETED,
            MissionState.FAILED,
            MissionState.ABORTED,
        }
        if mission.state not in _restartable:
            raise ValueError(f"Boundary cannot be changed while mission is {mission.state}")
        mission.boundary = boundary
        mission.area_m2 = boundary.area_m2
        mission.state = MissionState.DRAFT
        self.missions.update(mission)
        self.events.add(
            MissionEvent(
                mission_id=mission.id,
                type=EventType.BOUNDARY_IMPORTED,
                message=f"Boundary imported ({boundary.area_hectares:.2f} ha)",
                metadata={"area_m2": boundary.area_m2, "warnings": boundary.warnings},
            )
        )
        return mission

    def validate(self, mission_id: str) -> Mission:
        mission = self.require(mission_id)
        if mission.boundary is None:
            raise ValueError("Mission boundary has not been imported")
        mission.state = MissionState.VALIDATED
        self.missions.update(mission)
        self.events.add(
            MissionEvent(
                mission_id=mission.id,
                type=EventType.MISSION_VALIDATED,
                message="Mission boundary validated; Stage 3 planning is not yet available",
                metadata={"warnings": mission.boundary.warnings},
            )
        )
        return mission

    def set_simulation_progress(
        self, mission_id: str, state: MissionState, elapsed: float, coverage: float
    ) -> Mission:
        mission = self.require(mission_id)
        if state is MissionState.RUNNING and mission.started_at is None:
            mission.started_at = datetime.now(UTC)
        mission.state = state
        mission.elapsed_seconds = max(0, elapsed)
        mission.coverage_percent = min(100, max(0, coverage))
        if state is MissionState.COMPLETED and mission.completed_at is None:
            mission.completed_at = datetime.now(UTC)
        return self.missions.update(mission)

    def set_execution_progress(
        self, mission_id: str, state: MissionState, elapsed: float, coverage: float
    ) -> Mission:
        """Update normalized progress from SITL or a future vehicle source."""
        return self.set_simulation_progress(mission_id, state, elapsed, coverage)

    def review(self, mission_id: str) -> dict[str, object]:
        mission = self.require(mission_id)
        vehicles = self.vehicles.list_for_mission(mission_id)
        targets = self.targets.list_for_mission(mission_id)
        events = self.events.list_for_mission(mission_id)
        warnings = [event for event in events if event.severity.value in {"WARNING", "ERROR", "CRITICAL"}]
        active_targets = [target for target in targets if target.state.value != "REJECTED"]
        delivered = [target for target in active_targets if target.delivery_state.value == "DELIVERED"]
        confirmed = [target for target in active_targets if target.state.value not in {"CANDIDATE", "REJECTED"}]
        start_battery = 100.0 if vehicles else None
        end_battery = min((vehicle.battery_percent for vehicle in vehicles), default=None)
        return {
            "mission": mission,
            "targets_found": len(active_targets),
            "targets_confirmed": len(confirmed),
            "deliveries_completed": len(delivered),
            "vehicle_start_battery": start_battery,
            "vehicle_end_battery": end_battery,
            "warnings": warnings,
            "events": events,
        }
