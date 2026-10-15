"""Async GCS bridge for one real ArduCopter SITL execution at a time."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.models import SyntheticSurvivorScenario

from ..config import GroundConfig
from ..domain.events import MissionEvent
from ..domain.execution import ExecutionState, MissionExecution
from ..domain.mission import GeoPoint, MissionState
from ..repositories.contracts import EventRepository, ExecutionRepository, PlanRepository, VehicleRepository
from ..services.mission_service import MissionService
from ..services.telemetry_service import RealtimeEnvelope
from ..services.websocket_manager import WebSocketManager
from .adapter import SITLAutopilotAdapter
from .artifacts import Stage3ArtifactWriter
from .launcher import SITLLauncher, SITLLaunchError
from .surveillance_service import SurveillanceService
from .runner import SITLMissionRunner
from .rescue_artifacts import Stage7SArtifactWriter
from .rescue_runner import SITLRescueMissionRunner
from .telemetry import SITLTelemetrySnapshot


class SITLManager:
    def __init__(
        self,
        config: GroundConfig,
        repository_root: Path,
        missions: MissionService,
        plans: PlanRepository,
        executions: ExecutionRepository,
        vehicles: VehicleRepository,
        events: EventRepository,
        websocket: WebSocketManager,
        geolocation_service=None,
    ):
        self.config = config
        self.repository_root = repository_root
        self.missions = missions
        self.plans = plans
        self.executions = executions
        self.vehicles = vehicles
        self.events = events
        self.websocket = websocket
        self.geolocation_service = geolocation_service
        self.launcher = SITLLauncher(
            ardupilot_home=config.sitl.ardupilot_home,
            vehicle=config.sitl.vehicle,
            frame=config.sitl.frame,
            speedup=config.sitl.launcher_speedup,
            wsl_distribution=config.sitl.wsl_distribution,
        )
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.runners: dict[str, Any] = {}
        self.surveillance = None
        self.latest_run_directory: Path | None = None
        self.rescue_config = load_rescue_config(repository_root / "config" / "rescue_orchestration.yaml")
        if self.geolocation_service is not None:
            self.geolocation_service.on_target_confirmed = self._on_target_confirmed

    def _on_target_confirmed(self, estimate: Any) -> None:
        runner = self.runners.get(estimate.mission_id)
        if runner is not None and hasattr(runner, "queue_target"):
            runner.queue_target(estimate)

    def ensure_enabled(self) -> None:
        if self.config.competition.enabled or not self.config.sitl.enabled:
            raise PermissionError("SITL engineering APIs are disabled")

    def start_process(self, mission_id: str, altitude_m: float, heading_deg: float = 0) -> dict[str, object]:
        self.ensure_enabled()
        mission = self.missions.require(mission_id)
        if mission.boundary is None:
            raise ValueError("Mission boundary is required to place SITL near the mission")
        location = GeoPoint(
            latitude=mission.boundary.origin.latitude,
            longitude=mission.boundary.origin.longitude,
            altitude_m=altitude_m,
        )
        log_path = self.repository_root / "data" / "runs" / "sitl_launcher.log"
        identity = self.launcher.start(location, heading_deg, log_path)
        return {"status": "started", "source": identity.__dict__, "log": str(log_path)}

    def stop_process(self) -> dict[str, str]:
        self.ensure_enabled()
        self.launcher.stop()
        return {"status": "stopped"}

    async def stop_surveillance(self) -> None:
        """Release the camera receiver and its WSL bridge for the active run.

        The Simulation Lab owns Gazebo/SITL, while this manager owns the
        bridge.  Stopping only the lab used to leave the bridge connected to
        a dead world until the mission task happened to unwind.
        """
        if self.surveillance is not None:
            await self.surveillance.stop()
            self.surveillance = None

    async def execute(
        self, mission_id: str, connection_override: str | None = None,
        simulation_owner: Any | None = None,
    ) -> dict[str, str]:
        self.ensure_enabled()
        task = self.tasks.get(mission_id)
        if task and not task.done():
            return {"status": "already_running", "mission_id": mission_id}
        mission = self.missions.require(mission_id)
        plan = self.plans.get(mission_id)
        if mission.boundary is None or plan is None or not plan.validation.valid:
            raise ValueError("A persisted, validated coverage plan is required before SITL execution")
        
        if simulation_owner is None and not self.launcher.status()["running"]:
            altitude = mission.boundary.origin.altitude_m
            if altitude is None:
                altitude = 0.0
            try:
                self.start_process(mission_id, altitude)
            except SITLLaunchError:
                # Ardupilot not found locally; assume it's running externally.
                pass

        loop = asyncio.get_running_loop()
        artifacts = Stage7SArtifactWriter(self.repository_root / "data", mission_id)
        self.latest_run_directory = artifacts.run_directory
        adapter = SITLAutopilotAdapter(
            connection_override or self.launcher.connection or self.config.sitl.connection,
            self.config.sitl.environment,
            self.config.sitl.command_authority,
            self.config.sitl.heartbeat_timeout_s,
            self.config.sitl.command_timeout_s,
        )
        from ..domain.flight_parameters import FlightParameters
        flight_parameters = getattr(mission, "flight_parameters", None) or FlightParameters()
        runner = SITLRescueMissionRunner(
            adapter,
            self.rescue_config,
            self.config.sitl.readiness_timeout_s,
            self.config.sitl.mission_timeout_s,
            self.config.sitl.rtl_timeout_s,
            self.config.sitl.message_rates_hz,
            flight_parameters=flight_parameters,
            telemetry_callback=lambda snapshot, execution: self._telemetry(loop, mission_id, snapshot, execution),
            event_callback=lambda event: self._event(loop, event),
            artifacts=artifacts,
        )
        self.runners[mission_id] = runner
        source = self.launcher.detect()
        
        await self._start_surveillance(
            mission_id, self._camera_topic(simulation_owner), mission.boundary.origin,
            self._camera_profile(simulation_owner),
        )

        self.tasks[mission_id] = asyncio.create_task(
            self._run(mission_id, runner, plan, mission.boundary, source.commit if source else None, simulation_owner),
            name=f"sitl-mission-{mission_id}",
        )
        return {"status": "started", "mission_id": mission_id}

    async def execute_rescue(
        self, mission_id: str, scenario: SyntheticSurvivorScenario,
        *, auto_start_sitl: bool = True, connection_override: str | None = None,
    ) -> dict[str, str]:
        """One high-level Stage 7S action; still engineering-only and SITL-guarded."""
        self.ensure_enabled()
        task = self.tasks.get(mission_id)
        if task and not task.done():
            return {"status": "already_running", "mission_id": mission_id}
        mission = self.missions.require(mission_id)
        plan = self.plans.get(mission_id)
        if mission.boundary is None or plan is None or not plan.validation.valid:
            raise ValueError("A persisted, validated coverage plan is required before rescue execution")
        if auto_start_sitl and not self.launcher.status()["running"]:
            altitude = mission.boundary.origin.altitude_m
            if altitude is None:
                altitude = 0.0
            try:
                self.start_process(mission_id, altitude)
            except SITLLaunchError:
                pass
        loop = asyncio.get_running_loop()
        artifacts = Stage7SArtifactWriter(self.repository_root / "data", mission_id)
        self.latest_run_directory = artifacts.run_directory
        adapter = SITLAutopilotAdapter(
            connection_override or self.config.sitl.connection, self.config.sitl.environment,
            self.config.sitl.command_authority, self.config.sitl.heartbeat_timeout_s,
            self.config.sitl.command_timeout_s,
        )
        from ..domain.flight_parameters import FlightParameters
        flight_parameters = getattr(mission, "flight_parameters", None) or FlightParameters()
        runner = SITLRescueMissionRunner(
            adapter, self.rescue_config,
            self.config.sitl.readiness_timeout_s, self.config.sitl.mission_timeout_s,
            self.config.sitl.rtl_timeout_s, self.config.sitl.message_rates_hz,
            flight_parameters=flight_parameters,
            telemetry_callback=lambda snapshot, execution: self._telemetry(loop, mission_id, snapshot, execution),
            event_callback=lambda event: self._event(loop, event), artifacts=artifacts,
        )
        self.runners[mission_id] = runner
        source = self.launcher.detect()

        await self._start_surveillance(mission_id)

        self.tasks[mission_id] = asyncio.create_task(
            self._run_rescue(
                mission_id, runner, plan, mission.boundary, scenario,
                source.commit if source else None,
            ),
            name=f"sitl-rescue-{mission_id}",
        )
        return {"status": "started", "mission_id": mission_id, "run_directory": str(artifacts.run_directory)}

    async def _run_rescue(self, mission_id, runner, plan, boundary, scenario, git_commit) -> None:
        execution = await asyncio.to_thread(runner.execute, plan, boundary, scenario, git_commit)
        self.plans.upsert(plan)
        self.executions.upsert(execution)
        target_data = execution.metadata.get("target")
        if target_data:
            from ..domain.target import DeliveryState, Target, TargetState
            payload = execution.metadata.get("payload") or {}
            delivered = bool(payload.get("verification_succeeded"))
            target = Target(
                target_id=target_data["target_id"], mission_id=mission_id,
                state=TargetState.DELIVERED if delivered else TargetState.CONFIRMED,
                latitude=target_data["latitude"], longitude=target_data["longitude"],
                east_m=target_data["east_m"], north_m=target_data["north_m"],
                detection_confidence=target_data["detection_confidence"],
                geolocation_uncertainty_m=target_data["uncertainty_m"],
                observation_count=target_data["observation_count"],
                first_seen_at=target_data["first_seen_at"], last_seen_at=target_data["last_seen_at"],
                delivery_state=DeliveryState.DELIVERED if delivered else DeliveryState.FAILED,
                assigned_vehicle_id="drone_01", simulated=True,
                geolocation_quality=target_data.get("quality"),
                inlier_count=target_data.get("inlier_count"), outlier_count=target_data.get("outlier_count"),
                evidence_references=target_data.get("evidence_references", []),
            )
            self.missions.targets.upsert(target)
        broadcast_state = MissionState.COMPLETED if execution.state is ExecutionState.COMPLETED else MissionState.FAILED
        # Reset to VALIDATED after failure so the operator can retry immediately.
        persist_state = broadcast_state if broadcast_state is MissionState.COMPLETED else MissionState.VALIDATED
        elapsed = (
            (execution.completed_at - execution.started_at).total_seconds()
            if execution.started_at and execution.completed_at else 0
        )
        self.missions.set_execution_progress(mission_id, persist_state, elapsed, execution.actual_coverage_percent)
        await self.websocket.broadcast(RealtimeEnvelope(
            type="mission.progress", mission_id=mission_id, vehicle_id="drone_01",
            sequence=execution.current_mission_item,
            payload={
                "execution_source": "SITL", "execution_state": execution.state,
                "rescue_state": execution.metadata.get("rescue_state"),
                "state": broadcast_state, "elapsed_seconds": elapsed,
                "coverage_percent": execution.actual_coverage_percent,
                "payload_remaining": execution.metadata.get("payload_inventory_remaining"),
            },
        ))
        if self.surveillance is not None and self.surveillance.mission_id == mission_id:
            await self.surveillance.stop()
            self.surveillance = None

    async def _run(self, mission_id, runner, plan, boundary, git_commit, simulation_owner=None) -> None:
        try:
            scenarios = []
            if simulation_owner is not None:
                definition = getattr(simulation_owner, "definition", None)
                if definition and hasattr(definition, "survivors") and definition.survivors:
                    survivor_items = getattr(definition.survivors, "survivors", [])
                    from rescue_core.autonomy.models import SyntheticSurvivorScenario
                    from ..services.coordinates import LocalCoordinateFrame
                    from ..domain.mission import GeoPoint
                    frame = LocalCoordinateFrame(boundary.origin)
                    for item in survivor_items:
                        lat = getattr(item, "latitude", None)
                        lon = getattr(item, "longitude", None)
                        sid = getattr(item, "survivor_id", None) or getattr(item, "id", None)
                        if lat is not None and lon is not None and sid:
                            enu = frame.to_enu(GeoPoint(latitude=lat, longitude=lon, altitude_m=float(getattr(item, "up_m", 0.0) or 0.0)))
                            scenarios.append(SyntheticSurvivorScenario(
                                target_id=sid,
                                true_east_m=enu.east_m,
                                true_north_m=enu.north_m,
                                true_latitude=lat,
                                true_longitude=lon,
                                visibility_distance_m=50.0,
                                detection_probability=1.0,
                                coarse_estimate_offset_east_m=0.0,
                                coarse_estimate_offset_north_m=0.0,
                                delivery_required=True,
                            ))
            execution = await asyncio.to_thread(runner.execute, plan, boundary, scenarios if scenarios else None, git_commit)
            # The autopilot-specific fingerprint is known only after mission building.
            self.plans.upsert(plan)
            self.executions.upsert(execution)
            broadcast_state = {
                ExecutionState.COMPLETED: MissionState.COMPLETED,
                ExecutionState.ABORTED: MissionState.ABORTED,
            }.get(execution.state, MissionState.FAILED)
            # Reset to VALIDATED after failure/abort so the operator can retry
            # immediately without being locked out by the boundary guard.
            # COMPLETED and ABORTED stay as-is (their semantics are meaningful).
            persist_state = broadcast_state if broadcast_state is MissionState.COMPLETED else MissionState.VALIDATED
            elapsed = (
                (execution.completed_at - execution.started_at).total_seconds()
                if execution.started_at and execution.completed_at else 0
            )
            self.missions.set_execution_progress(mission_id, persist_state, elapsed, execution.actual_coverage_percent)
            await self.websocket.broadcast(
                RealtimeEnvelope(
                    type="mission.progress", mission_id=mission_id, vehicle_id="drone_01",
                    sequence=execution.current_mission_item,
                    payload={
                        "execution_source": "SITL",
                        "execution_state": execution.state,
                        "state": broadcast_state,
                        "elapsed_seconds": elapsed,
                        "coverage_percent": execution.actual_coverage_percent,
                        "current_mission_item": execution.current_mission_item,
                        "total_mission_items": execution.total_mission_items,
                    },
                )
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[SITL RUNNER EXCEPTION] {e}", flush=True)
        finally:
            if self.surveillance is not None and self.surveillance.mission_id == mission_id:
                await self.surveillance.stop()
                self.surveillance = None
            if simulation_owner is not None:
                await asyncio.to_thread(simulation_owner.stop, completed=True)

    def abort(self, mission_id: str) -> dict[str, str]:
        self.ensure_enabled()
        return self.safety_recall(mission_id)

    def safety_recall(self, mission_id: str) -> dict[str, str]:
        """Allowed operator safety surface; the existing runner is already SITL-guarded."""
        runner = self.runners.get(mission_id)
        if runner is None:
            raise ValueError("No active SITL mission exists")
        if not isinstance(runner, (SITLMissionRunner, SITLRescueMissionRunner)):
            raise PermissionError("Safety recall refuses a non-SITL mission runner")
        runner.abort()
        return {"status": "rtl_requested", "mission_id": mission_id}

    def status(self, mission_id: str | None = None) -> dict[str, object]:
        execution = self.executions.get(mission_id) if mission_id else None
        task = self.tasks.get(mission_id) if mission_id else None
        surveillance_info = None
        if self.surveillance is not None:
            surveillance_info = {
                "mission_id": self.surveillance.mission_id,
                "is_running": self.surveillance.is_running,
                "frame_count": self.surveillance.frame_count,
                "decoded_frame_count": self.surveillance.decoded_frame_count,
                "inference_count": self.surveillance.inference_count,
                "latest_error": self.surveillance.latest_error,
            }
        return {
            "launcher": self.launcher.status(),
            "connection": self.config.sitl.connection,
            "mission_running": bool(task and not task.done()),
            "execution": execution.model_dump(mode="json") if execution else None,
            "latest_run_directory": str(self.latest_run_directory) if self.latest_run_directory else None,
            "surveillance": surveillance_info,
        }

    async def shutdown(self) -> None:
        for runner in self.runners.values():
            runner.abort()
        for task in self.tasks.values():
            if not task.done():
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(task, timeout=5)
        self.launcher.stop()
        if self.surveillance is not None:
            await self.surveillance.stop()
            self.surveillance = None

    @staticmethod
    def _camera_topic(simulation_owner: Any | None) -> str | None:
        if simulation_owner is None:
            return None
        topic = getattr(simulation_owner, "readiness", {}).get("camera_topic")
        return str(topic) if topic else None

    @staticmethod
    def _camera_profile(simulation_owner: Any | None) -> Any | None:
        definition = getattr(simulation_owner, "definition", None)
        return getattr(definition, "camera", None)

    async def _start_surveillance(
        self, mission_id: str, camera_topic: str | None = None, mission_origin=None, camera_profile=None,
    ) -> None:
        """Start one mission-scoped bridge before flight execution begins.

        Awaiting startup removes the old race where the mission could finish
        before the TCP listener and WSL bridge had become usable.  A prior,
        completed mission cannot retain its old mission ID or frames.
        """
        if self.surveillance is not None:
            await self.surveillance.stop()
        self.surveillance = SurveillanceService(
            self.repository_root, self.websocket, camera_topic=camera_topic,
            mission_origin=mission_origin, camera_profile=camera_profile,
            geolocation_service=self.geolocation_service,
        )
        await self.surveillance.start(mission_id)

    def _telemetry(
        self, loop: asyncio.AbstractEventLoop, mission_id: str,
        snapshot: SITLTelemetrySnapshot, execution: MissionExecution,
    ) -> None:
        if self.surveillance is not None and self.surveillance.mission_id == mission_id:
            self.surveillance.update_vehicle_pose(snapshot.vehicle, execution.state)
        
        now = datetime.now(UTC)
        # Throttle realtime telemetry broadcast to 5Hz to prevent WebSocket flooding and browser drops
        if not hasattr(self, "_last_telemetry_broadcast"):
            self._last_telemetry_broadcast = {}
        last_tx = self._last_telemetry_broadcast.get(mission_id)
        should_broadcast = last_tx is None or (now - last_tx).total_seconds() >= 0.2
        if not hasattr(self, "_diag_counts"):
            self._diag_counts = {"raw": 0, "broadcast": 0}
        self._diag_counts["raw"] += 1

        if should_broadcast:
            vehicle = self.vehicles.upsert(mission_id, snapshot.vehicle)
            self._last_telemetry_broadcast[mission_id] = now
            self._diag_counts["broadcast"] += 1
            if self._diag_counts["raw"] % 100 == 0:
                print(f"[PIPELINE DIAGNOSTIC] RAW SITL samples received: {self._diag_counts['raw']} -> Backend WS broadcasts: {self._diag_counts['broadcast']}", flush=True)
            
            if vehicle.sequence % 10 == 0:
                self.vehicles.persist_telemetry(mission_id, vehicle)
                self.executions.upsert(execution)
            elapsed = (datetime.now(UTC) - execution.started_at).total_seconds() if execution.started_at else 0
            broad_state = (
                MissionState.RETURNING
                if execution.state in {ExecutionState.RETURNING, ExecutionState.LANDING}
                else MissionState.RUNNING
            )
            self.missions.set_execution_progress(mission_id, broad_state, elapsed, execution.actual_coverage_percent)
            asyncio.run_coroutine_threadsafe(
                self.websocket.broadcast(
                    RealtimeEnvelope(
                        type="vehicle.telemetry", mission_id=mission_id, vehicle_id=vehicle.vehicle_id,
                        sequence=vehicle.sequence,
                        payload={
                            **vehicle.model_dump(mode="json"), "source": "SITL",
                            "current_mission_item": snapshot.current_mission_item,
                            "total_mission_items": snapshot.total_mission_items,
                        },
                    )
                ), loop,
            )
        
            # Only broadcast mission progress at 1Hz or if it's explicitly triggered elsewhere
            if not hasattr(self, "_last_progress_broadcast"):
                self._last_progress_broadcast = {}
            last_prog = self._last_progress_broadcast.get(mission_id)
            should_prog = last_prog is None or (now - last_prog).total_seconds() >= 1.0
            if should_prog:
                self._last_progress_broadcast[mission_id] = now
                asyncio.run_coroutine_threadsafe(
                    self.websocket.broadcast(
                        RealtimeEnvelope(
                            type="mission.progress", mission_id=mission_id, vehicle_id=vehicle.vehicle_id,
                            sequence=vehicle.sequence,
                            payload={
                                "execution_source": "SITL", "execution_state": execution.state,
                                "rescue_state": execution.metadata.get("rescue_state"),
                                "state": broad_state, "elapsed_seconds": elapsed,
                                "coverage_percent": execution.actual_coverage_percent,
                                # Vehicle telemetry supplies the newest coordinate; the
                                # frontend owns its bounded trail instead of receiving
                                # the entire path again on every update.
                                "actual_trail_point": (
                                    execution.actual_trail[-1].model_dump(mode="json")
                                    if execution.actual_trail else None
                                ),
                            },
                        )
                    ), loop,
                )

    def _event(self, loop: asyncio.AbstractEventLoop, event: MissionEvent) -> None:
        self.events.add(event)
        envelope_type = "system.warning" if event.severity.value != "INFO" else "mission.state"
        payload = event.model_dump(mode="json")
        if event.type.value == "RESCUE_STATE_CHANGED":
            envelope_type = "rescue.state_changed"
        elif event.type.value == "TARGET_CONFIRMED" and isinstance(event.metadata.get("target"), dict):
            from ..domain.target import DeliveryState, Target, TargetState
            value = event.metadata["target"]
            target_id = value["target_id"]
            existing = self.missions.targets.get(target_id)
            current_state = TargetState.CONFIRMED
            current_delivery_state = DeliveryState.PENDING
            deliv_e = None
            deliv_n = None
            if existing is not None:
                if existing.state == TargetState.DELIVERED:
                    current_state = TargetState.DELIVERED
                if existing.delivery_state != DeliveryState.PENDING:
                    current_delivery_state = existing.delivery_state
                deliv_e = existing.delivery_point_east_m
                deliv_n = existing.delivery_point_north_m

            target = Target(
                target_id=target_id, mission_id=event.mission_id,
                state=current_state, latitude=value["latitude"], longitude=value["longitude"],
                east_m=value["east_m"], north_m=value["north_m"],
                detection_confidence=value["detection_confidence"],
                geolocation_uncertainty_m=value.get("geolocation_uncertainty_m", value.get("uncertainty_m", 1.0)),
                observation_count=value.get("observation_count", 4),
                first_seen_at=value["first_seen_at"], last_seen_at=value["last_seen_at"],
                delivery_state=current_delivery_state,
                delivery_point_east_m=deliv_e,
                delivery_point_north_m=deliv_n,
                assigned_vehicle_id="drone_01",
                simulated=True, geolocation_quality=value.get("quality"),
                inlier_count=value.get("inlier_count"), outlier_count=value.get("outlier_count"),
                evidence_references=value.get("evidence_references", []),
            )
            self.missions.targets.upsert(target)
            envelope_type = "target.confirmed"
            payload = target.model_dump(mode="json")
            runner = self.runners.get(event.mission_id)
            if runner is not None and hasattr(runner, "queue_target_data"):
                runner.queue_target_data(value)
        elif event.type.value == "DELIVERY_QUEUED" and event.target_id:
            from ..domain.target import DeliveryState
            target = self.missions.targets.get(event.target_id)
            if target:
                target.delivery_state = DeliveryState.DELIVERY_QUEUED
                self.missions.targets.upsert(target)
                envelope_type = "target.updated"
                payload = target.model_dump(mode="json")
        elif event.type.value == "APPROACH_STARTED" and event.target_id:
            from ..domain.target import DeliveryState
            target = self.missions.targets.get(event.target_id)
            if target and target.delivery_state != DeliveryState.DELIVERED_VERIFIED:
                target.delivery_state = DeliveryState.APPROACHING
                if "delivery_point_enu" in event.metadata:
                    deliv_enu = event.metadata["delivery_point_enu"]
                    if isinstance(deliv_enu, (list, tuple)) and len(deliv_enu) >= 2:
                        target.delivery_point_east_m = float(deliv_enu[0])
                        target.delivery_point_north_m = float(deliv_enu[1])
                self.missions.targets.upsert(target)
                envelope_type = "target.updated"
                payload = target.model_dump(mode="json")
        elif event.type.value == "DELIVERY_RELEASED" and event.target_id:
            from ..domain.target import DeliveryState
            target = self.missions.targets.get(event.target_id)
            if target and target.delivery_state != DeliveryState.DELIVERED_VERIFIED:
                target.delivery_state = DeliveryState.RELEASED
                self.missions.targets.upsert(target)
                envelope_type = "target.updated"
                payload = target.model_dump(mode="json")
        elif event.type.value in {"DELIVERY_VERIFIED", "DELIVERY_FAILED"} and event.target_id:
            from ..domain.target import DeliveryState, TargetState
            target = self.missions.targets.get(event.target_id)
            if target:
                delivered = event.type.value == "DELIVERY_VERIFIED"
                target.state = TargetState.DELIVERED if delivered else TargetState.CONFIRMED
                target.delivery_state = DeliveryState.DELIVERED_VERIFIED if delivered else DeliveryState.FAILED
                if "delivery_point_enu" in event.metadata:
                    deliv_enu = event.metadata["delivery_point_enu"]
                    if isinstance(deliv_enu, (list, tuple)) and len(deliv_enu) >= 2:
                        target.delivery_point_east_m = float(deliv_enu[0])
                        target.delivery_point_north_m = float(deliv_enu[1])
                self.missions.targets.upsert(target)
                envelope_type = "target.updated"
                payload = target.model_dump(mode="json")
        asyncio.run_coroutine_threadsafe(
            self.websocket.broadcast(
                RealtimeEnvelope(
                    type=envelope_type,
                    mission_id=event.mission_id, vehicle_id=event.vehicle_id, sequence=0,
                    payload=payload,
                )
            ), loop,
        )
