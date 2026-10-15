"""Async simulator orchestration and normalized event publication."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from ..domain.events import EventType, MissionEvent
from ..domain.mission import MissionState
from ..domain.target import DeliveryState, TargetState
from ..repositories.contracts import EventRepository, TargetRepository, VehicleRepository
from ..services.mission_service import MissionService
from ..services.telemetry_service import RealtimeEnvelope
from ..services.websocket_manager import WebSocketManager
from .drone_simulator import SimulatedVehicleSource, SimulatorUpdate


class SimulatorManager:
    def __init__(
        self,
        mission_service: MissionService,
        vehicles: VehicleRepository,
        targets: TargetRepository,
        events: EventRepository,
        websocket: WebSocketManager,
        seed: int,
        total_steps: int,
        interval_seconds: float,
        telemetry_persist_interval_seconds: float,
    ):
        self.mission_service = mission_service
        self.vehicles = vehicles
        self.targets = targets
        self.events = events
        self.websocket = websocket
        self.seed = seed
        self.total_steps = total_steps
        self.interval_seconds = interval_seconds
        self.persist_every_steps = max(1, round(telemetry_persist_interval_seconds / interval_seconds))
        self.sources: dict[str, SimulatedVehicleSource] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def source(self, mission_id: str) -> SimulatedVehicleSource:
        source = self.sources.get(mission_id)
        if source is None:
            mission = self.mission_service.require(mission_id)
            source = SimulatedVehicleSource(mission, self.seed, self.total_steps)
            self.sources[mission_id] = source
        return source

    async def start(self, mission_id: str) -> dict[str, str]:
        source = self.source(mission_id)
        source.paused = False
        running = self.tasks.get(mission_id)
        if running and not running.done():
            return {"status": "already_running", "mission_id": mission_id}
        self.tasks[mission_id] = asyncio.create_task(self._run(mission_id), name=f"gcs-sim-{mission_id}")
        return {"status": "started", "mission_id": mission_id}

    async def _run(self, mission_id: str) -> None:
        source = self.source(mission_id)
        while source.step_index < source.total_steps:
            if source.paused:
                await asyncio.sleep(self.interval_seconds)
                continue
            update = source.next_update()
            if update:
                await self.process(mission_id, update)
            await asyncio.sleep(self.interval_seconds)

    async def process(self, mission_id: str, update: SimulatorUpdate) -> None:
        vehicle = self.vehicles.upsert(mission_id, update.vehicle)
        if vehicle.sequence % self.persist_every_steps == 0 or update.mission_state is MissionState.COMPLETED:
            self.vehicles.persist_telemetry(mission_id, vehicle)
        mission = self.mission_service.set_simulation_progress(
            mission_id, update.mission_state, update.elapsed_seconds, update.coverage_percent
        )
        await self.websocket.broadcast(
            RealtimeEnvelope(
                type="vehicle.telemetry",
                mission_id=mission_id,
                vehicle_id=vehicle.vehicle_id,
                sequence=vehicle.sequence,
                payload=vehicle.model_dump(mode="json"),
            )
        )
        await self.websocket.broadcast(
            RealtimeEnvelope(
                type="mission.progress",
                mission_id=mission_id,
                vehicle_id=vehicle.vehicle_id,
                sequence=vehicle.sequence,
                payload={
                    "state": mission.state,
                    "elapsed_seconds": mission.elapsed_seconds,
                    "coverage_percent": mission.coverage_percent,
                },
            )
        )
        for target in update.targets:
            existing = self.targets.get(target.target_id)
            self.targets.upsert(target)
            await self.websocket.broadcast(
                RealtimeEnvelope(
                    type="target.updated" if existing else "target.created",
                    mission_id=mission_id,
                    vehicle_id=vehicle.vehicle_id,
                    sequence=vehicle.sequence,
                    payload=target.model_dump(mode="json"),
                )
            )
            if target.delivery_state is not DeliveryState.PENDING:
                await self.websocket.broadcast(
                    RealtimeEnvelope(
                        type="delivery.updated",
                        mission_id=mission_id,
                        vehicle_id=vehicle.vehicle_id,
                        sequence=vehicle.sequence,
                        payload={"target_id": target.target_id, "state": target.delivery_state},
                    )
                )
        for event in update.events:
            self.events.add(event)
            family = "system.warning" if event.severity.value != "INFO" else "mission.state"
            await self.websocket.broadcast(
                RealtimeEnvelope(
                    type=family,
                    mission_id=mission_id,
                    vehicle_id=event.vehicle_id,
                    sequence=vehicle.sequence,
                    payload=event.model_dump(mode="json"),
                )
            )

    def pause(self, mission_id: str) -> dict[str, str]:
        self.source(mission_id).paused = True
        return {"status": "paused", "mission_id": mission_id}

    def resume(self, mission_id: str) -> dict[str, str]:
        self.source(mission_id).paused = False
        return {"status": "resumed", "mission_id": mission_id}

    async def reset(self, mission_id: str) -> dict[str, str]:
        task = self.tasks.pop(mission_id, None)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.source(mission_id).reset()
        return {"status": "reset", "mission_id": mission_id}

    async def inject_target(self, mission_id: str) -> dict[str, object]:
        source = self.source(mission_id)
        target = source.inject_target()
        self.targets.upsert(target)
        event = MissionEvent(
            mission_id=mission_id,
            vehicle_id="drone_01",
            target_id=target.target_id,
            type=EventType.TARGET_CANDIDATE,
            message="Operator injected a simulated target candidate",
            simulated=True,
        )
        self.events.add(event)
        await self.websocket.broadcast(
            RealtimeEnvelope(
                type="target.created",
                mission_id=mission_id,
                vehicle_id="drone_01",
                sequence=source.step_index,
                payload=target.model_dump(mode="json"),
            )
        )
        return target.model_dump(mode="json")

    async def confirm_target(self, mission_id: str) -> dict[str, object]:
        source = self.source(mission_id)
        if source.target is None:
            raise ValueError("No simulated target exists")
        source.target.state = TargetState.CONFIRMED
        source.target.observation_count += 1
        source.target.last_seen_at = datetime.now(UTC)
        self.targets.upsert(source.target)
        event = source._event(
            EventType.TARGET_CONFIRMED,
            "Operator confirmed a simulated target",
            source.target.target_id,
        )
        self.events.add(event)
        await self.websocket.broadcast(
            RealtimeEnvelope(
                type="target.updated",
                mission_id=mission_id,
                vehicle_id="drone_01",
                sequence=source.step_index,
                payload=source.target.model_dump(mode="json"),
            )
        )
        return source.target.model_dump(mode="json")

    async def simulate_delivery(self, mission_id: str) -> dict[str, object]:
        source = self.source(mission_id)
        if source.target is None:
            raise ValueError("No simulated target exists")
        source.target.state = TargetState.DELIVERED
        source.target.delivery_state = DeliveryState.DELIVERED
        self.targets.upsert(source.target)
        event = source._event(
            EventType.DELIVERY_COMPLETED,
            "Operator completed a simulated delivery event",
            source.target.target_id,
        )
        self.events.add(event)
        await self.websocket.broadcast(
            RealtimeEnvelope(
                type="delivery.updated",
                mission_id=mission_id,
                vehicle_id="drone_01",
                sequence=source.step_index,
                payload={"target_id": source.target.target_id, "state": source.target.delivery_state},
            )
        )
        return source.target.model_dump(mode="json")

    async def complete(self, mission_id: str) -> dict[str, str]:
        source = self.source(mission_id)
        task = self.tasks.pop(mission_id, None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        while source.step_index < source.total_steps:
            source.paused = False
            update = source.next_update()
            if update:
                await self.process(mission_id, update)
        return {"status": "completed", "mission_id": mission_id}

    async def shutdown(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
