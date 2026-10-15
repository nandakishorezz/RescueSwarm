"""SQL database schema usable with SQLite and PostgreSQL/PostGIS deployments."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class MissionRecord(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    area_m2: Mapped[float | None] = mapped_column(Float)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0)
    coverage_percent: Mapped[float] = mapped_column(Float, default=0)


class MissionBoundaryRecord(Base):
    __tablename__ = "mission_boundaries"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    geometry_wgs84: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    area_m2: Mapped[float] = mapped_column(Float, nullable=False)
    area_hectares: Mapped[float] = mapped_column(Float, nullable=False)
    origin: Mapped[dict] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class MissionFlightParametersRecord(Base):
    __tablename__ = "mission_flight_parameters"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class MissionPlanRecord(Base):
    __tablename__ = "mission_plans"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    planner_version: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mission_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class MissionExecutionRecord(Base):
    __tablename__ = "mission_executions"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    mission_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class VehicleRecord(Base):
    __tablename__ = "vehicles"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class TelemetryRecord(Base):
    __tablename__ = "telemetry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(80), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    relative_altitude_m: Mapped[float] = mapped_column(Float)
    battery_percent: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class TargetRecord(Base):
    __tablename__ = "targets"
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(20), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DeliveryRecord(Base):
    __tablename__ = "deliveries"
    delivery_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.target_id"), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventRecord(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String(80), index=True)
    target_id: Mapped[str | None] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Database:
    def __init__(self, url: str):
        engine_options: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False}
            if url in {"sqlite://", "sqlite:///:memory:"}:
                engine_options["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                path = Path(url.removeprefix("sqlite:///"))
                path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **engine_options)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def health(self) -> tuple[bool, str]:
        try:
            with self.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return True, "Database connection ready"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
