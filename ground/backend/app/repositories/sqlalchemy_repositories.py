"""SQLAlchemy repository implementations shared by SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.events import MissionEvent
from ..domain.execution import MissionExecution
from ..domain.flight_parameters import FlightParameters
from ..domain.mission import Boundary, Mission
from ..domain.target import Target
from ..domain.vehicle import VehicleState
from ..planning.models import CoveragePlan
from .database import (
    EventRecord,
    MissionExecutionRecord,
    MissionBoundaryRecord,
    MissionFlightParametersRecord,
    MissionPlanRecord,
    MissionRecord,
    TargetRecord,
    TelemetryRecord,
    VehicleRecord,
)


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class SqlAlchemyMissionRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def _write(record: MissionRecord, mission: Mission) -> None:
        record.name = mission.name
        record.state = mission.state.value
        record.created_at = mission.created_at
        record.started_at = mission.started_at
        record.completed_at = mission.completed_at
        record.area_m2 = mission.area_m2
        record.elapsed_seconds = mission.elapsed_seconds
        record.coverage_percent = mission.coverage_percent

    @staticmethod
    def _read(session: Session, record: MissionRecord) -> Mission:
        boundary_record = session.get(MissionBoundaryRecord, record.id)
        boundary = Boundary.model_validate(
            {
                "vertices": boundary_record.geometry_wgs84["vertices"],
                "source_format": boundary_record.source_format,
                "area_m2": boundary_record.area_m2,
                "area_hectares": boundary_record.area_hectares,
                "origin": boundary_record.origin,
                "warnings": boundary_record.warnings,
            }
        ) if boundary_record else None
        param_record = session.get(MissionFlightParametersRecord, record.id)
        flight_parameters = (
            FlightParameters.model_validate(param_record.payload)
            if param_record and param_record.payload
            else FlightParameters()
        )
        return Mission(
            id=record.id,
            name=record.name,
            state=record.state,
            created_at=_utc(record.created_at),
            started_at=_utc(record.started_at),
            completed_at=_utc(record.completed_at),
            boundary=boundary,
            flight_parameters=flight_parameters,
            area_m2=record.area_m2,
            elapsed_seconds=record.elapsed_seconds,
            coverage_percent=record.coverage_percent,
        )

    def add(self, mission: Mission) -> Mission:
        with self.sessions.begin() as session:
            record = MissionRecord(id=mission.id)
            self._write(record, mission)
            session.add(record)
            if getattr(mission, "flight_parameters", None):
                session.add(
                    MissionFlightParametersRecord(
                        mission_id=mission.id,
                        payload=mission.flight_parameters.model_dump(mode="json"),
                    )
                )
        return mission

    def get(self, mission_id: str) -> Mission | None:
        with self.sessions() as session:
            record = session.get(MissionRecord, mission_id)
            return self._read(session, record) if record else None

    def list(self) -> list[Mission]:
        with self.sessions() as session:
            records = session.scalars(select(MissionRecord).order_by(desc(MissionRecord.created_at))).all()
            return [self._read(session, record) for record in records]

    def update(self, mission: Mission) -> Mission:
        with self.sessions.begin() as session:
            record = session.get(MissionRecord, mission.id)
            if record is None:
                raise KeyError(f"Mission not found: {mission.id}")
            self._write(record, mission)
            if mission.boundary:
                boundary_record = session.get(MissionBoundaryRecord, mission.id)
                if boundary_record is None:
                    boundary_record = MissionBoundaryRecord(mission_id=mission.id)
                    session.add(boundary_record)
                boundary_record.geometry_wgs84 = {
                    "vertices": [point.model_dump(mode="json") for point in mission.boundary.vertices]
                }
                boundary_record.source_format = mission.boundary.source_format
                boundary_record.area_m2 = mission.boundary.area_m2
                boundary_record.area_hectares = mission.boundary.area_hectares
                boundary_record.origin = mission.boundary.origin.model_dump(mode="json")
                boundary_record.warnings = mission.boundary.warnings
            if getattr(mission, "flight_parameters", None):
                param_record = session.get(MissionFlightParametersRecord, mission.id)
                if param_record is None:
                    param_record = MissionFlightParametersRecord(
                        mission_id=mission.id,
                        payload=mission.flight_parameters.model_dump(mode="json"),
                    )
                    session.add(param_record)
                else:
                    param_record.payload = mission.flight_parameters.model_dump(mode="json")
        return mission


class SqlAlchemyVehicleRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def upsert(self, mission_id: str, vehicle: VehicleState) -> VehicleState:
        with self.sessions.begin() as session:
            record = session.get(VehicleRecord, (mission_id, vehicle.vehicle_id))
            if record is None:
                record = VehicleRecord(mission_id=mission_id, vehicle_id=vehicle.vehicle_id)
                session.add(record)
            record.name = vehicle.name
            record.state = vehicle.model_dump(mode="json")
            record.last_update = vehicle.last_update
        return vehicle

    def list_for_mission(self, mission_id: str) -> list[VehicleState]:
        with self.sessions() as session:
            records = session.scalars(
                select(VehicleRecord).where(VehicleRecord.mission_id == mission_id)
            ).all()
            return [VehicleState.model_validate(record.state) for record in records]

    def persist_telemetry(self, mission_id: str, vehicle: VehicleState) -> None:
        with self.sessions.begin() as session:
            session.add(
                TelemetryRecord(
                    mission_id=mission_id,
                    vehicle_id=vehicle.vehicle_id,
                    timestamp=vehicle.last_update,
                    sequence=vehicle.sequence,
                    latitude=vehicle.latitude,
                    longitude=vehicle.longitude,
                    relative_altitude_m=vehicle.relative_altitude_m,
                    battery_percent=vehicle.battery_percent,
                    payload=vehicle.model_dump(mode="json"),
                )
            )


class SqlAlchemyTargetRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def upsert(self, target: Target) -> Target:
        with self.sessions.begin() as session:
            record = session.get(TargetRecord, target.target_id)
            if record is None:
                record = TargetRecord(target_id=target.target_id, mission_id=target.mission_id)
                session.add(record)
            record.state = target.state.value
            record.delivery_state = target.delivery_state.value
            record.data = target.model_dump(mode="json")
            record.last_seen_at = target.last_seen_at
        return target

    def get(self, target_id: str) -> Target | None:
        with self.sessions() as session:
            record = session.get(TargetRecord, target_id)
            return Target.model_validate(record.data) if record else None

    def list_for_mission(self, mission_id: str) -> list[Target]:
        with self.sessions() as session:
            records = session.scalars(
                select(TargetRecord).where(TargetRecord.mission_id == mission_id)
            ).all()
            return [Target.model_validate(record.data) for record in records]


class SqlAlchemyEventRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def add(self, event: MissionEvent) -> MissionEvent:
        with self.sessions.begin() as session:
            # Internal retries and outbox replay are at-least-once. The event ID
            # is the idempotency key, so duplicate delivery/failsafe events are
            # acknowledged without a second database row.
            if session.get(EventRecord, event.event_id) is not None:
                return event
            session.add(
                EventRecord(
                    event_id=event.event_id,
                    mission_id=event.mission_id,
                    vehicle_id=event.vehicle_id,
                    target_id=event.target_id,
                    type=event.type.value,
                    severity=event.severity.value,
                    timestamp=event.timestamp,
                    message=event.message,
                    metadata_json=event.metadata,
                    simulated=event.simulated,
                )
            )
        return event

    def list_for_mission(self, mission_id: str, limit: int = 500) -> list[MissionEvent]:
        with self.sessions() as session:
            records = session.scalars(
                select(EventRecord)
                .where(EventRecord.mission_id == mission_id)
                .order_by(desc(EventRecord.timestamp))
                .limit(limit)
            ).all()
            return [
                MissionEvent(
                    event_id=record.event_id,
                    mission_id=record.mission_id,
                    vehicle_id=record.vehicle_id,
                    target_id=record.target_id,
                    type=record.type,
                    severity=record.severity,
                    timestamp=_utc(record.timestamp),
                    message=record.message,
                    metadata=record.metadata_json,
                    simulated=record.simulated,
                )
                for record in reversed(records)
            ]


class SqlAlchemyPlanRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def upsert(self, plan: CoveragePlan) -> CoveragePlan:
        if not plan.plan_fingerprint:
            raise ValueError("Coverage plan must have a plan fingerprint before persistence")
        with self.sessions.begin() as session:
            record = session.get(MissionPlanRecord, plan.mission_id)
            if record is None:
                record = MissionPlanRecord(mission_id=plan.mission_id)
                session.add(record)
            record.planner_version = plan.planner_version
            record.plan_fingerprint = plan.plan_fingerprint
            record.mission_fingerprint = plan.mission_fingerprint
            record.created_at = plan.created_at
            record.payload = plan.model_dump(mode="json")
        return plan

    def get(self, mission_id: str) -> CoveragePlan | None:
        with self.sessions() as session:
            record = session.get(MissionPlanRecord, mission_id)
            return CoveragePlan.model_validate(record.payload) if record else None


class SqlAlchemyExecutionRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def upsert(self, execution: MissionExecution) -> MissionExecution:
        with self.sessions.begin() as session:
            record = session.get(MissionExecutionRecord, execution.mission_id)
            if record is None:
                record = MissionExecutionRecord(mission_id=execution.mission_id)
                session.add(record)
            record.source = execution.source.value
            record.state = execution.state.value
            record.mission_fingerprint = execution.mission_fingerprint
            record.updated_at = execution.updated_at
            record.payload = execution.model_dump(mode="json")
        return execution

    def get(self, mission_id: str) -> MissionExecution | None:
        with self.sessions() as session:
            record = session.get(MissionExecutionRecord, mission_id)
            return MissionExecution.model_validate(record.payload) if record else None
