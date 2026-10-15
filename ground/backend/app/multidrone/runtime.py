"""One opt-in, simulation-only fleet owner; no single-drone runner is reused as a singleton."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.runner import SITLMissionRunner
from ground.backend.app.sitl.surveillance_service import SurveillanceService
from rescue_core.simulation.orchestration import SimulationManager
from shapely.geometry import Point, Polygon

from .contracts import DroneRole, FleetConfiguration, FleetSession, default_configuration
from .coordinator import ExclusiveServiceCoordinator
from .registry import SharedSurvivorRegistry
from .service_executor import MultiDroneServiceExecutor
from .state_machine import (
    DroneLifecycleState,
    DroneStateMachine,
    FleetState,
    FleetStateMachine,
    FLEET_STATE_EXPLANATIONS,
)


def fleet_rtl_budget_s(configured_s: float, observed_camera_rtf: float) -> float:
    """Bounded fleet-only wall budget for two rendered, inferred Gazebo cameras.

    In the first live fleet stop, the existing 180 s guard expired near home
    with both vehicles still airborne (one descending at 0.47 m/s). Source
    camera frames per nominal 30 Hz provide a conservative measured speed
    proxy. This changes only the wall guard, never the landing/disarm gates.
    """
    if configured_s <= 0 or observed_camera_rtf <= 0:
        raise ValueError("RTL budget requires positive configuration and observed throughput")
    slowdown = .2 / max(.08, min(1.0, observed_camera_rtf))
    return min(600.0, max(configured_s, configured_s * slowdown * 2.0))


class MultiDroneRuntime:
    """Owns exactly one generated world, its SITL children, and two camera bridges.

    Phase 1.1 manages authoritative fleet and per-drone lifecycle states.
    Phase 2 survivor coordination remains uninstantiated in this phase.
    """

    def __init__(self, root, config, missions, planning, simulation_lab, sitl_manager, websocket):
        self.root, self.config = root, config
        self.missions, self.planning = missions, planning
        self.simulation_lab, self.sitl_manager = simulation_lab, sitl_manager
        self.websocket = websocket
        self.simulation = SimulationManager(root / "data" / "simulation_runs")
        self.session: FleetSession | None = None
        self.plan = None
        self.state_machine = FleetStateMachine(FleetState.IDLE)
        self.drone_state_machines: dict[str, DroneStateMachine] = {}
        self.runners: dict[str, SITLMissionRunner] = {}
        self.cameras: dict[str, SurveillanceService] = {}
        self.camera_history: dict[str, dict] = {}
        self.camera_started_at: dict[str, float] = {}
        self.vehicles: dict[str, dict] = {}
        self.failed_vehicles: list[str] = []
        self.trails: dict[str, list[list[float]]] = {}
        self.events: list[dict] = []
        self.observations: list[dict] = []
        self.registry: SharedSurvivorRegistry | None = None
        self.coordinator: ExclusiveServiceCoordinator | None = None
        self.final_coordination_snapshot: dict | None = None
        self.error: str | None = None
        self.task: asyncio.Task | None = None
        self.stop_requested = False
        self._lock = asyncio.Lock()

    def _get_vehicles_snapshot(self) -> dict[str, dict]:
        now = time.monotonic()
        result = {}
        for vid, source in getattr(self, "vehicles", {}).items():
            item = dict(source)
            stamp = item.get("last_update_monotonic")
            item["telemetry_age_s"] = round(now - stamp, 2) if stamp else None
            item["connected"] = stamp is not None and (now - stamp <= 3.0)
            result[vid] = item
        return result

    @staticmethod
    def _port_in_use(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                return True
        except OSError:
            return False

    def _check_resources(self, count: int) -> None:
        if getattr(self.simulation_lab, "processes", None) is not None:
            raise RuntimeError("Single-drone Simulation Lab owns the active world")
        if any(not task.done() for task in self.sitl_manager.tasks.values()):
            raise RuntimeError("Single-drone mission is active")
        ports = [5760 + 10 * i for i in range(count)] + [5761] + [45678 + i for i in range(count)]
        occupied = [port for port in ports if self._port_in_use(port)]
        if occupied:
            raise RuntimeError(f"Fleet ports already in use: {occupied}")

    def _get_state_machine(self) -> FleetStateMachine:
        if not hasattr(self, "state_machine"):
            raw_state = getattr(self.session, "state", FleetState.IDLE) if getattr(self, "session", None) else FleetState.IDLE
            try:
                initial = FleetState(raw_state)
            except ValueError:
                initial = FleetState.IDLE
            self.state_machine = FleetStateMachine(initial)
        return self.state_machine

    def _transition_drone(self, vehicle_id: str, new_state: DroneLifecycleState, error: str | None = None) -> None:
        if not hasattr(self, "drone_state_machines"):
            self.drone_state_machines = {}
        if vehicle_id not in self.drone_state_machines:
            self.drone_state_machines[vehicle_id] = DroneStateMachine(vehicle_id, DroneLifecycleState.CONFIGURED)
        sm = self.drone_state_machines[vehicle_id]
        if sm.can_transition(new_state):
            sm.transition(new_state, error)
        if hasattr(self, "vehicles") and vehicle_id in self.vehicles:
            self.vehicles[vehicle_id]["state"] = sm.current.value
            self.vehicles[vehicle_id]["state_updated_at"] = sm.updated_at.isoformat()
            if error is not None:
                self.vehicles[vehicle_id]["error"] = error

    def _add_event(
        self,
        vehicle_id: str | None,
        event_type: str,
        message: str,
        severity: str = "INFO",
        metadata: dict | None = None,
    ) -> None:
        if not hasattr(self, "events"):
            self.events = []
        self.events.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "vehicle_id": vehicle_id,
            "type": event_type,
            "message": message,
            "severity": severity,
            "metadata": metadata or {},
        })
        self.events = self.events[-100:]
        if hasattr(self, "vehicles") and vehicle_id and vehicle_id in self.vehicles:
            self.vehicles[vehicle_id]["last_event"] = event_type

    async def start(self, configuration: FleetConfiguration) -> dict:
        sm = self._get_state_machine()
        async with self._lock:
            if sm.current not in {FleetState.IDLE, FleetState.STOPPED, FleetState.ERROR}:
                raise RuntimeError("A fleet session is already active")
            if getattr(self, "task", None) and not self.task.done():
                raise RuntimeError("A fleet session is already active")

            try:
                sm.transition(FleetState.VALIDATING, "Checking configuration")
                self._add_event(None, "FLEET_START_REQUESTED", "Fleet startup requested")

                if self.config.competition.enabled or not self.config.fleet.enabled:
                    raise PermissionError("Fleet simulation is engineering-only")
                self._check_resources(len(configuration.drones))
                mission = self.missions.require(configuration.mission_id)
                global_plan = self.planning.get(configuration.mission_id)
                source = self.simulation_lab.definition
                if source is None and getattr(self.simulation_lab, "processes", None) is None:
                    self.simulation_lab.restore_prepared_run(configuration.mission_id)
                    source = self.simulation_lab.definition
                if mission.boundary is None or global_plan is None or not global_plan.validation.valid:
                    raise ValueError("A validated Mission coverage plan is required")
                if source is None or source.mission.mission_id != configuration.mission_id:
                    raise ValueError("Prepare this Mission's scenario in Simulation Lab before fleet start")
                vehicle_ids = [drone.vehicle_id for drone in configuration.drones]
                self.plan = FleetCoveragePartitioner().partition(global_plan, vehicle_ids)
            except Exception as exc:
                self.error = str(exc)
                if sm.can_transition(FleetState.ERROR):
                    sm.transition(FleetState.ERROR, f"Configuration validation failed: {exc}")
                self._add_event(None, "FLEET_ERROR", f"Configuration validation failed: {exc}", severity="ERROR")
                raise

            flight_snapshot = (
                mission.flight_parameters.model_dump(mode="json")
                if getattr(mission, "flight_parameters", None) is not None else None
            )
            session_id = uuid4()
            run_id = f"multidrone_{session_id.hex}"
            self.drone_state_machines = {item.vehicle_id: DroneStateMachine(item.vehicle_id) for item in configuration.drones}
            self.vehicles = {item.vehicle_id: {
                "vehicle_id": item.vehicle_id, "role": item.role.value,
                "system_id": item.system_id, "connection": f"tcp:127.0.0.1:{5760 + 10 * item.instance_id}",
                "state": DroneLifecycleState.CONFIGURED.value,
                "state_updated_at": datetime.now(UTC).isoformat(),
                "telemetry": None, "progress_percent": 0,
                "route_configuration": self.plan.vehicle_plans[item.vehicle_id].configuration.model_dump(mode="json"),
                "flight_parameters_snapshot": flight_snapshot,
                "sector_ids": next(
                    assignment.segment_ids for assignment in self.plan.assignments
                    if assignment.vehicle_id == item.vehicle_id
                ),
                "last_update_monotonic": None, "last_event": None,
                "error": None,
            } for item in configuration.drones}
            self.session = FleetSession(
                session_id=session_id, mission_id=configuration.mission_id,
                run_id=run_id, state=FleetState.STARTING.value, configuration=configuration,
                started_at=datetime.now(UTC),
            )
            definition = source.model_copy(update={
                "run_id": run_id, "fleet_count": len(configuration.drones),
                "fleet_spawn_offset_m": configuration.spawn_spacing_m,
                "phase4_payload_slots": 0,
                "disable_servicer_camera": configuration.disable_servicer_camera,
            })
            sm.transition(FleetState.STARTING, "Launching vehicles")
            self.session.state = sm.current.value
            self.events = []
            self.observations = []
            self.final_coordination_snapshot = None
            self.registry = SharedSurvivorRegistry(
                session_id=session_id,
                mission_id=configuration.mission_id,
                on_event_callback=self._add_event,
            )
            self.coordinator = ExclusiveServiceCoordinator(
                session=self.session,
                fleet_config=configuration,
                registry=self.registry,
                vehicles_state_supplier=self._get_vehicles_snapshot,
                event_callback=self._add_event,
            )
            self.registry.on_confirmed_callback = self.coordinator.on_survivor_confirmed
            self.camera_history = {}
            self.camera_started_at = {}
            self.trails = {item.vehicle_id: [] for item in configuration.drones}
            self.error = None
            self.failed_vehicles = []
            self.stop_requested = False
            self._add_event(None, "FLEET_VALIDATED", "Fleet configuration validated and accepted")
            self.task = asyncio.create_task(self._run(definition, mission.boundary), name=f"fleet-{session_id}")
            return self.status()

    async def _run(self, definition, boundary) -> None:
        assert self.session is not None and self.plan is not None
        session = self.session
        sm = self._get_state_machine()
        try:
            for vid in self.vehicles:
                self._transition_drone(vid, DroneLifecycleState.STARTING)
            self._add_event(None, "SIMULATION_PREPARING", "Preparing simulation environment")
            await asyncio.to_thread(self.simulation.prepare, definition)
            if self.stop_requested:
                return

            self._add_event(None, "SIMULATION_STARTING", "Launching simulation processes")
            await asyncio.to_thread(self.simulation.start)
            if self.simulation.adapter:
                self.simulation.adapter.disconnect()
                self.simulation.adapter = None
            if self.stop_requested:
                return

            sm.transition(FleetState.WAITING_FOR_TELEMETRY, "Waiting for vehicle connections")
            session.state = sm.current.value
            for vid in self.vehicles:
                self._transition_drone(vid, DroneLifecycleState.WAITING_FOR_HEARTBEAT)

            inference_lock = threading.Lock()
            shared_model = None
            frame = LocalCoordinateFrame(boundary.origin)
            if self.plan is not None and getattr(self.plan, "safe_polygon_enu", None):
                safe_poly = Polygon([
                    (p.east_m, p.north_m) for p in self.plan.safe_polygon_enu
                ])
                region_validator = lambda east, north: safe_poly.covers(Point(east, north))
            else:
                boundary_poly = Polygon([
                    (frame.to_enu(p).east_m, frame.to_enu(p).north_m) for p in boundary.vertices
                ])
                region_validator = lambda east, north: boundary_poly.covers(Point(east, north))

            for item in session.configuration.drones:
                if definition.disable_servicer_camera and item.role == DroneRole.SERVICER:
                    continue
                topic = f"/{item.vehicle_id}/{definition.camera.camera_id}/image"
                await asyncio.to_thread(self.simulation.launcher.wait_for_topic, topic, 45)
                camera = SurveillanceService(
                    self.root, self.websocket, port=45678 + item.instance_id,
                    camera_topic=topic, vehicle_id=item.vehicle_id,
                    precise_helper_stop=True,
                    mission_origin=boundary.origin, camera_profile=definition.camera,
                    observation_callback=lambda vid, observation, estimate: self._observation(
                        session.session_id, vid, observation, estimate,
                    ),
                    model_override=shared_model, inference_lock=inference_lock,
                    mission_region_validator=region_validator,
                )
                self.cameras[item.vehicle_id] = camera
                self.camera_started_at[item.vehicle_id] = time.monotonic()
                await camera.start(str(session.session_id))
                if shared_model is None:
                    shared_model = camera.model

            if self.stop_requested:
                return

            loop = asyncio.get_running_loop()
            results = []
            service_executor = MultiDroneServiceExecutor(self)
            for item in session.configuration.drones:
                vehicle_id = item.vehicle_id
                adapter = SITLAutopilotAdapter(
                    self.vehicles[vehicle_id]["connection"], "sitl", "simulation_only",
                    self.config.sitl.heartbeat_timeout_s, self.config.sitl.command_timeout_s,
                )
                hook = service_executor.execute_service_cycle if item.role == DroneRole.SERVICER else None
                runner = SITLMissionRunner(
                    adapter, self.config.sitl.readiness_timeout_s,
                    self.config.sitl.mission_timeout_s, self.config.sitl.rtl_timeout_s,
                    self.config.sitl.message_rates_hz,
                    vehicle_id=vehicle_id, expected_system_id=item.system_id,
                    telemetry_callback=lambda snapshot, execution, vid=vehicle_id: loop.call_soon_threadsafe(
                        self._telemetry, session.session_id, vid, snapshot, execution,
                    ),
                    event_callback=lambda event, vid=vehicle_id: loop.call_soon_threadsafe(
                        self._event, session.session_id, vid, event,
                    ),
                    service_hook=hook,
                )
                self.runners[vehicle_id] = runner
                results.append(asyncio.to_thread(runner.execute, self.plan.vehicle_plans[vehicle_id], boundary))

            executions = await asyncio.gather(*results, return_exceptions=True)
            for vehicle_id, result in zip(self.runners, executions, strict=True):
                if isinstance(result, Exception):
                    self._transition_drone(vehicle_id, DroneLifecycleState.FAILED, str(result))
                elif getattr(result, "state", None) and result.state.value == "FAILED":
                    self._transition_drone(vehicle_id, DroneLifecycleState.FAILED, "; ".join(getattr(result, "errors", [])) or "Runner failed")
                else:
                    self._transition_drone(vehicle_id, DroneLifecycleState.DISARMED)
                    if hasattr(result, "actual_coverage_percent"):
                        self.vehicles[vehicle_id]["progress_percent"] = result.actual_coverage_percent

        except Exception as exc:
            self.error = str(exc)
            if sm.can_transition(FleetState.ERROR):
                sm.transition(FleetState.ERROR, f"Startup or runtime failure: {exc}")
                session.state = sm.current.value
            self._add_event(None, "FLEET_ERROR", f"Fleet failure: {exc}", severity="ERROR")
        finally:
            await self._cleanup(session)

    async def _cleanup(self, session: FleetSession) -> None:
        sm = self._get_state_machine()
        if sm.can_transition(FleetState.STOPPING):
            sm.transition(FleetState.STOPPING, "Cleaning up simulation resources")
            session.state = sm.current.value

        for camera in getattr(self, "cameras", {}).values():
            if not hasattr(self, "camera_history"):
                self.camera_history = {}
            self.camera_history[camera.vehicle_id] = {
                "camera_frame_count": camera.frame_count,
                "camera_decoded_count": camera.decoded_frame_count,
                "camera_inference_count": camera.inference_count,
                "camera_last_frame_at": camera.last_frame_at,
                "camera_model_status": "ERROR" if camera.model_error else "READY",
            }
            try:
                await camera.stop()
            except Exception as exc:
                self.error = f"camera cleanup: {exc}"
        if hasattr(self, "cameras"):
            self.cameras.clear()

        try:
            is_completed = session.state == "COMPLETED" or sm.current == FleetState.STOPPED
            await asyncio.to_thread(self.simulation.stop, is_completed)
        except Exception as exc:
            if not getattr(self, "error", None):
                self.error = f"simulation stop: {exc}"

        for vid in getattr(self, "vehicles", {}):
            if self.vehicles[vid].get("state") != DroneLifecycleState.FAILED.value:
                self._transition_drone(vid, DroneLifecycleState.STOPPED)

        session.ended_at = datetime.now(UTC)
        # Keep a read-only result for the stopped session; never carry its
        # authoritative registry or assignments into a new run.
        self.final_coordination_snapshot = {
            "session_id": session.session_id,
            "targets": [r.model_dump(mode="json") for r in self.registry.confirmed_survivors()]
            if getattr(self, "registry", None) else [],
            "assignments": [a.model_dump(mode="json") for a in self.coordinator.assignments()]
            if getattr(self, "coordinator", None) else [],
        }
        if getattr(self, "registry", None) is not None:
            self.registry.reset()
            self.registry = None
        if getattr(self, "coordinator", None) is not None:
            self.coordinator.reset()
            self.coordinator = None
        if sm.current != FleetState.ERROR:
            if sm.can_transition(FleetState.STOPPED):
                sm.transition(FleetState.STOPPED, "Fleet stopped")
            session.state = sm.current.value
        self._add_event(None, "FLEET_STOPPED", "Fleet session stopped and resources cleaned up")

    def _telemetry(self, session_id, vehicle_id, snapshot, execution) -> None:
        if getattr(self, "session", None) is None or self.session.session_id != session_id:
            return
        vehicle = self.vehicles[vehicle_id]
        vehicle["telemetry"] = snapshot.vehicle.model_dump(mode="json")
        vehicle["progress_percent"] = getattr(execution, "actual_coverage_percent", 0.0)
        vehicle["current_mission_item"] = getattr(execution, "current_mission_item", 0)
        vehicle["total_mission_items"] = getattr(execution, "total_mission_items", 0)
        vehicle["last_update_monotonic"] = time.monotonic()
        errors = getattr(execution, "errors", None)
        if errors:
            vehicle["error"] = "; ".join(errors)

        mode = getattr(snapshot.vehicle, "flight_mode", None)
        armed = getattr(snapshot.vehicle, "armed", False)
        alt = getattr(snapshot.vehicle, "relative_altitude_m", 0.0)
        current_item = getattr(snapshot, "current_mission_item", 0)
        total_items = getattr(execution, "total_mission_items", 0)
        exec_state = getattr(getattr(execution, "state", None), "value", None)
        landed_state = getattr(snapshot, "landed_state", None)
        rtl_completed = getattr(execution, "rtl_completed", False)

        searching = (
            3 <= current_item < max(3, total_items - 1)
            and mode == "AUTO"
            and armed
            and alt >= 0.5
        )

        if exec_state == "FAILED":
            self._transition_drone(vehicle_id, DroneLifecycleState.FAILED, vehicle.get("error"))
        elif exec_state in {"RETURNING", "ABORTED"} or mode == "RTL":
            if exec_state == "LANDING" or landed_state == 2:
                self._transition_drone(vehicle_id, DroneLifecycleState.LANDING)
            elif rtl_completed or landed_state == 1:
                if not armed:
                    self._transition_drone(vehicle_id, DroneLifecycleState.DISARMED)
                else:
                    self._transition_drone(vehicle_id, DroneLifecycleState.LANDED)
            else:
                self._transition_drone(vehicle_id, DroneLifecycleState.RETURNING)
        elif searching or exec_state == "SEARCHING":
            self._transition_drone(vehicle_id, DroneLifecycleState.SEARCHING)
        elif exec_state in {"SITL_READY", "MISSION_VERIFIED", "ARMING", "TAKING_OFF"}:
            self._transition_drone(vehicle_id, DroneLifecycleState.READY)

        latitude, longitude = getattr(snapshot.vehicle, "latitude", None), getattr(snapshot.vehicle, "longitude", None)
        trail = self.trails.setdefault(vehicle_id, [])
        if latitude and longitude and (
            not trail or abs(trail[-1][0] - longitude) + abs(trail[-1][1] - latitude) > 1e-8
        ):
            trail.append([longitude, latitude])
            del trail[:-1000]

        camera = getattr(self, "cameras", {}).get(vehicle_id)
        if camera:
            camera.update_vehicle_pose(snapshot.vehicle, getattr(execution, "state", None))
            runner = getattr(self, "runners", {}).get(vehicle_id)
            elapsed = time.monotonic() - getattr(self, "camera_started_at", {}).get(vehicle_id, time.monotonic())
            fps = float(self.simulation.definition.camera.fps.value) if (getattr(self, "simulation", None) and getattr(self.simulation, "definition", None)) else 30.0
            if runner and elapsed > 20 and camera.frame_count > 30 and fps > 0:
                observed_rtf = camera.frame_count / (fps * elapsed)
                runner.rtl_timeout_s = fleet_rtl_budget_s(self.config.sitl.rtl_timeout_s, observed_rtf)
                vehicle["camera_observed_rtf"] = round(observed_rtf, 3)
                vehicle["rtl_budget_s"] = round(runner.rtl_timeout_s, 1)

        self._reconcile_fleet_state()
        if getattr(self, "coordinator", None) is not None:
            try:
                self.coordinator.on_telemetry_or_state_change()
            except Exception as exc:
                self._add_event(vehicle_id, "COORDINATOR_ERROR", f"Coordinator reconcile error: {exc}", severity="WARNING")

    def _observation(self, session_id, vehicle_id, observation, estimate) -> None:
        if getattr(self, "session", None) is None or self.session.session_id != session_id:
            return
        if not hasattr(self, "observations"):
            self.observations = []
        obs_time = (
            observation.timestamp.isoformat() if hasattr(observation, "timestamp") else (
                observation.observed_at.isoformat() if hasattr(observation, "observed_at") else None
            )
        )
        self.observations.append({
            "vehicle_id": vehicle_id, "frame_id": getattr(observation, "frame_id", ""),
            "observed_at": obs_time,
            "track_id": getattr(observation, "target_track_id", getattr(observation, "track_id", None)),
            "confirmed": estimate is not None and getattr(estimate.state, "value", estimate.state) == "CONFIRMED",
        })
        self.observations = self.observations[-200:]

        if getattr(self, "registry", None) is not None:
            try:
                self.registry.ingest(vehicle_id, observation, estimate)
            except Exception as exc:
                self._add_event(vehicle_id, "REGISTRY_ERROR", f"Registry observation ingest error: {exc}", severity="WARNING")

    def _event(self, session_id, vehicle_id, event) -> None:
        if getattr(self, "session", None) is None or self.session.session_id != session_id:
            return
        data = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        ev_type = data.get("type")
        msg = data.get("message", "")
        sev = data.get("severity", "INFO")
        if not hasattr(self, "events"):
            self.events = []
        self.events.append({
            "timestamp": data.get("timestamp") or datetime.now(UTC).isoformat(),
            "vehicle_id": vehicle_id,
            "type": ev_type,
            "message": msg,
            "severity": sev,
            "metadata": data.get("metadata", {}),
        })
        self.events = self.events[-100:]
        if hasattr(self, "vehicles") and vehicle_id in self.vehicles:
            self.vehicles[vehicle_id]["last_event"] = ev_type

        if ev_type in {"SITL_READY", "MISSION_VERIFIED"}:
            self._transition_drone(vehicle_id, DroneLifecycleState.READY)
        elif ev_type in {"SEARCH_STARTED", "SEARCH_PROGRESS"}:
            self._transition_drone(vehicle_id, DroneLifecycleState.SEARCHING)
        elif ev_type == "RTL_STARTED":
            self._transition_drone(vehicle_id, DroneLifecycleState.RETURNING)
        elif ev_type == "VEHICLE_LANDED":
            self._transition_drone(vehicle_id, DroneLifecycleState.LANDED)
        elif ev_type == "VEHICLE_DISARMED":
            self._transition_drone(vehicle_id, DroneLifecycleState.DISARMED)
            if hasattr(self, "vehicles") and vehicle_id in self.vehicles:
                telemetry = self.vehicles[vehicle_id].get("telemetry")
                if telemetry is not None:
                    telemetry["armed"] = False
        elif ev_type in {"SITL_CONNECTION_FAILED", "ARM_FAILED", "AUTO_FAILED", "MISSION_TIMEOUT", "LINK_LOST", "FAILSAFE"}:
            self._transition_drone(vehicle_id, DroneLifecycleState.FAILED, msg)

        self._reconcile_fleet_state()

    def _reconcile_fleet_state(self) -> None:
        if getattr(self, "session", None) is None:
            return
        sm = self._get_state_machine()
        current = sm.current
        if current in {FleetState.STOPPED, FleetState.STOPPING, FleetState.ERROR}:
            return

        if getattr(self, "stop_requested", False):
            all_landed_or_disarmed = all(
                self.vehicles[v].get("state") in {
                    DroneLifecycleState.LANDED.value,
                    DroneLifecycleState.DISARMED.value,
                    DroneLifecycleState.STOPPED.value,
                    DroneLifecycleState.FAILED.value,
                }
                for v in getattr(self, "vehicles", {})
            )
            if all_landed_or_disarmed:
                if sm.can_transition(FleetState.STOPPING):
                    sm.transition(FleetState.STOPPING, "Vehicles landed; stopping simulation")
            else:
                if current != FleetState.RETURNING and sm.can_transition(FleetState.RETURNING):
                    sm.transition(FleetState.RETURNING, "Vehicles returning safely")
            self.session.state = sm.current.value
            return

        now = time.monotonic()
        failed_vehicles = []
        stale_telemetry_vehicles = []
        stale_camera_vehicles = []
        all_searching = True
        any_operational = False
        all_failed = True

        vehicles = getattr(self, "vehicles", {})
        cameras = getattr(self, "cameras", {})

        for vid, v in vehicles.items():
            state = v.get("state")
            stamp = v.get("last_update_monotonic")
            telem_age = (now - stamp) if stamp else None
            camera = cameras.get(vid)
            cam_age = (time.time() - camera.last_frame_at) if (camera and getattr(camera, "last_frame_at", None)) else None

            if state == DroneLifecycleState.FAILED.value:
                failed_vehicles.append(vid)
            else:
                all_failed = False
                any_operational = True

            if state in {DroneLifecycleState.READY.value, DroneLifecycleState.SEARCHING.value}:
                if telem_age is not None and telem_age > 3.0:
                    stale_telemetry_vehicles.append(vid)
                if camera and cam_age is not None and cam_age > 3.0:
                    stale_camera_vehicles.append(vid)

            if state != DroneLifecycleState.SEARCHING.value:
                all_searching = False

        self.failed_vehicles = sorted(list(set(failed_vehicles + stale_telemetry_vehicles + stale_camera_vehicles)))

        if all_failed and len(vehicles) > 0:
            if sm.can_transition(FleetState.ERROR):
                sm.transition(FleetState.ERROR, "All fleet vehicles failed")
                self.session.state = sm.current.value
            return

        if current == FleetState.WAITING_FOR_TELEMETRY:
            if all_searching and any_operational:
                if sm.can_transition(FleetState.RUNNING):
                    sm.transition(FleetState.RUNNING, "Fleet active")
            elif failed_vehicles:
                if sm.can_transition(FleetState.DEGRADED):
                    sm.transition(FleetState.DEGRADED, f"Vehicle failure: {', '.join(failed_vehicles)}")
            self.session.state = sm.current.value
            return

        if current in {FleetState.RUNNING, FleetState.DEGRADED}:
            if failed_vehicles:
                reason = f"Vehicle failed: {', '.join(failed_vehicles)}"
                if current != FleetState.DEGRADED and sm.can_transition(FleetState.DEGRADED):
                    sm.transition(FleetState.DEGRADED, reason)
                else:
                    sm.reason = reason
            elif stale_telemetry_vehicles:
                reason = f"Telemetry stale for {', '.join(stale_telemetry_vehicles)}"
                if current != FleetState.DEGRADED and sm.can_transition(FleetState.DEGRADED):
                    sm.transition(FleetState.DEGRADED, reason)
                else:
                    sm.reason = reason
            elif stale_camera_vehicles:
                reason = f"Camera feed stale for {', '.join(stale_camera_vehicles)}"
                if current != FleetState.DEGRADED and sm.can_transition(FleetState.DEGRADED):
                    sm.transition(FleetState.DEGRADED, reason)
                else:
                    sm.reason = reason
            else:
                if current == FleetState.DEGRADED and sm.can_transition(FleetState.RUNNING):
                    sm.transition(FleetState.RUNNING, "Fleet active")
                    self._add_event(None, "FLEET_RECOVERED", "Fleet recovered to nominal running state")

        self.session.state = sm.current.value

    def authorize_service(self, vehicle_id: str) -> dict:
        if getattr(self, "session", None) is None or vehicle_id not in getattr(self, "vehicles", {}):
            raise ValueError("Vehicle is not in the active fleet")
        role = next(item.role for item in self.session.configuration.drones if item.vehicle_id == vehicle_id)
        if role is DroneRole.SEARCHER:
            raise PermissionError("SEARCHER has no approach, delivery, or payload-release capability")
        if getattr(self, "coordinator", None) is None:
            raise RuntimeError("SERVICER assignment requires the Phase-2 canonical coordinator; no delivery was authorized")
        active = [
            a for a in self.coordinator.assignments()
            if a.servicer_id == vehicle_id and a.state in {"ASSIGNED", "ACKNOWLEDGED"}
        ]
        if not active:
            raise RuntimeError(f"Vehicle {vehicle_id} has no active service assignment to authorize")
        assignment = active[0]
        if assignment.state == "ASSIGNED":
            self.coordinator.acknowledge_assignment(assignment.assignment_id)
        return {"status": "AUTHORIZED", "vehicle_id": vehicle_id, "assignment_id": str(assignment.assignment_id)}

    def stop_vehicle(self, vehicle_id: str) -> dict:
        if vehicle_id not in getattr(self, "runners", {}):
            raise ValueError("Vehicle runner is not active")
        self.runners[vehicle_id].abort()
        self._transition_drone(vehicle_id, DroneLifecycleState.RETURNING)
        self._add_event(vehicle_id, "RETURN_REQUESTED", f"Safe return requested for {vehicle_id}", severity="WARNING")

        # Abort any active service job for this vehicle: RTL takes precedence
        if getattr(self, "coordinator", None):
            active_aid = self.coordinator._servicer_to_assignment.get(vehicle_id)
            if active_aid:
                assignment = self.coordinator.get_assignment(active_aid)
                if assignment and assignment.state not in {"COMPLETED", "CANCELLED", "FAILED"}:
                    if not assignment.payload_released:
                        self.coordinator.requeue_assignment(active_aid, f"Safe return requested for {vehicle_id}")
                    else:
                        self.coordinator.fail_assignment(active_aid, f"Vehicle return requested after release; delivery unverified")

        self._reconcile_fleet_state()
        return self.status()

    async def stop(self) -> dict:
        sm = self._get_state_machine()
        if getattr(self, "session", None) and getattr(self, "task", None) and not self.task.done():
            self.stop_requested = True
            if sm.can_transition(FleetState.RETURNING):
                sm.transition(FleetState.RETURNING, "Safe fleet return requested")
                self.session.state = sm.current.value
            self._add_event(None, "RETURN_REQUESTED", "Safe fleet return requested by operator", severity="WARNING")

            # Fleet RTL/stop takes precedence over all service executions
            if getattr(self, "coordinator", None):
                for aid, assignment in list(self.coordinator._assignments.items()):
                    if assignment.state not in {"COMPLETED", "CANCELLED", "FAILED"}:
                        if not assignment.payload_released:
                            self.coordinator.cancel_assignment(aid, "Fleet return / stop requested by operator")
                        else:
                            self.coordinator.fail_assignment(aid, "Fleet stop requested after release")

            for vid, runner in getattr(self, "runners", {}).items():
                runner.abort()
                self._transition_drone(vid, DroneLifecycleState.RETURNING)
            self._reconcile_fleet_state()
        return self.status()

    async def shutdown(self) -> None:
        await self.stop()
        if getattr(self, "task", None):
            try:
                await asyncio.wait_for(self.task, timeout=240)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self.simulation.close)

    def status(self) -> dict:
        sm = self._get_state_machine()
        if getattr(self, "session", None) is None:
            return {
                "state": sm.current.value,
                "state_reason": sm.reason,
                "state_updated_at": sm.updated_at.isoformat(),
                "session": None,
                "vehicles": [],
                "failed_vehicles": [],
                "targets": [],
                "observations": [],
                "trails": {},
                "assignments": [],
                "deliveries": [],
                "events": list(getattr(self, "events", [])),
                "error": getattr(self, "error", None),
                "partition": None,
                "coordination_state": "PHASE_2_NOT_IMPLEMENTED",
            }

        self._reconcile_fleet_state()

        now = time.monotonic()
        vehicles = []
        for vehicle_id, source in getattr(self, "vehicles", {}).items():
            camera = getattr(self, "cameras", {}).get(vehicle_id)
            history = getattr(self, "camera_history", {}).get(vehicle_id, {})
            item = dict(source)
            stamp = item.pop("last_update_monotonic", None)
            item["telemetry_age_s"] = round(now - stamp, 2) if stamp else None
            item["connected"] = stamp is not None and (now - stamp < 3.0)
            item["camera_frame_count"] = camera.frame_count if camera else history.get("camera_frame_count", 0)
            item["camera_decoded_count"] = camera.decoded_frame_count if camera else history.get("camera_decoded_count", 0)
            item["camera_inference_count"] = camera.inference_count if camera else history.get("camera_inference_count", 0)
            last_frame_at = camera.last_frame_at if camera else history.get("camera_last_frame_at")
            item["camera_age_s"] = round(time.time() - last_frame_at, 2) if last_frame_at else None
            item["camera_model_status"] = (
                "ERROR" if camera and camera.model_error else "READY" if camera and camera.model
                else "DISABLED" if not camera and item.get("role") == "SERVICER"
                else history.get("camera_model_status", "WAITING")
            )
            item["camera_status"] = (
                "LIVE" if camera and item["camera_age_s"] is not None and item["camera_age_s"] < 3
                else "DISABLED" if not camera and item.get("role") == "SERVICER"
                else "STALE" if item["camera_frame_count"] else "NO_FRAMES"
            )
            vehicles.append(item)

        final = getattr(self, "final_coordination_snapshot", None)
        final = final if final and final.get("session_id") == self.session.session_id else {}
        try:
            targets = ([r.model_dump(mode="json") for r in self.registry.confirmed_survivors()]
                       if getattr(self, "registry", None) else list(final.get("targets", [])))
        except Exception:
            targets = []
        try:
            assignments = ([a.model_dump(mode="json") for a in self.coordinator.assignments()]
                           if getattr(self, "coordinator", None) else list(final.get("assignments", [])))
        except Exception:
            assignments = []

        deliveries = []
        for a in assignments:
            if a.get("payload_released") or a.get("delivery_verified") or a.get("state") in {"DELIVERED", "COMPLETED", "RESUMING_SEARCH"}:
                deliveries.append({
                    "assignment_id": a["assignment_id"],
                    "canonical_survivor_id": a["canonical_survivor_id"],
                    "servicer_id": a.get("servicer_id"),
                    "payload_released": a.get("payload_released", False),
                    "verified": a.get("delivery_verified", False),
                    "state": "VERIFIED" if a.get("delivery_verified") else "RELEASED" if a.get("payload_released") else a["state"],
                    "evidence": a.get("delivery_evidence"),
                    "standoff_distance_m": a.get("standoff_distance_m"),
                    "release_id": a.get("release_id"),
                })

        return {
            "state": sm.current.value,
            "state_reason": sm.reason,
            "state_updated_at": sm.updated_at.isoformat(),
            "session": self.session.model_dump(mode="json"),
            "vehicles": vehicles,
            "failed_vehicles": list(getattr(self, "failed_vehicles", [])),
            "targets": targets,
            "observations": list(getattr(self, "observations", [])),
            "trails": {vehicle: list(points) for vehicle, points in getattr(self, "trails", {}).items()},
            "assignments": assignments,
            "deliveries": deliveries,
            "events": list(getattr(self, "events", [])),
            "error": getattr(self, "error", None),
            "partition": self.plan.metrics.model_dump(mode="json") if getattr(self, "plan", None) else None,
            "coordination_state": "PHASE_2B_ACTIVE" if getattr(self, "coordinator", None)
            else "PHASE_2B_FINAL" if final else "PHASE_2_NOT_IMPLEMENTED",
        }


__all__ = ["MultiDroneRuntime", "default_configuration"]
