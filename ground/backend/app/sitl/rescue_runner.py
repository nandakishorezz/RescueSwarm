"""Real ArduCopter SITL AUTO→GUIDED→AUTO rescue mission execution."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import threading
from pymavlink import mavutil
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

from rescue_core.autonomy.alignment import PrecisionAlignmentController
from rescue_core.autonomy.approach import ApproachPoint, GuidedApproachPlanner, operational_flight_region
from rescue_core.autonomy.config import RescueOrchestrationConfig
from rescue_core.autonomy.models import ReacquisitionObservation, RescueMetrics, SyntheticSurvivorScenario
from rescue_core.autonomy.orchestrator import RescueMissionOrchestrator, RescueTransitionRecord
from rescue_core.autonomy.payload import PayloadInventory, SimulatedPayloadAdapter
from rescue_core.autonomy.policies import TargetEligibilityPolicy, TargetServiceQueue
from rescue_core.autonomy.reacquisition import ReacquisitionSearchPlanner, SyntheticReacquisitionProvider
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point
from rescue_core.autonomy.safety import GuidedSafetySnapshot, GuidedSafetySupervisor, require_sitl_command_adapter
from rescue_core.autonomy.states import RescueEvent, RescueState
from rescue_core.geolocation.models import FusedTargetEstimate, TargetTrackState
from ..domain.flight_parameters import FlightParameters
from .runner import execution_timing_for_plan

from ..domain.events import EventSeverity, EventType, MissionEvent
from ..domain.execution import ExecutionSource, ExecutionState, MissionExecution
from ..domain.mission import Boundary, GeoPoint
from ..planning.models import CoveragePlan
from ..services.coordinates import LocalCoordinateFrame
from .adapter import SITLAutopilotAdapter
from .coverage import ActualCoverageTracker
from .guided import SITLGuidedNavigator
from .mission import ArduCopterMissionBuilder
from .rescue_artifacts import Stage7SArtifactWriter
from .rescue_perception import SyntheticSearchTargetSource
from .telemetry import SITLTelemetrySnapshot

if False:  # pragma: no cover - type-only import without coupling Stage 7S to fleet runtime
    from ..fleet.scheduling import CoordinatedLandingGate


class RescueMissionError(RuntimeError):
    pass


class RescueAbortRequested(RuntimeError):
    """Internal control-flow signal: operator/safety recall closed the drop gate."""


class SITLRescueMissionRunner:
    """Stage 7S high-level runner; all commands pass through guarded SITL adapter."""

    def __init__(
        self, adapter: SITLAutopilotAdapter, rescue_config: RescueOrchestrationConfig,
        readiness_timeout_s: float, mission_timeout_s: float, rtl_timeout_s: float,
        message_rates_hz: dict[str, float],
        flight_parameters: FlightParameters | None = None,
        telemetry_callback: Callable[[SITLTelemetrySnapshot, MissionExecution], None] | None = None,
        event_callback: Callable[[MissionEvent], None] | None = None,
        artifacts: Stage7SArtifactWriter | None = None,
        payload: SimulatedPayloadAdapter | None = None,
        vehicle_id: str = "drone_01",
        landing_gate: "CoordinatedLandingGate | None" = None,
        target_service_authorizer: Callable[[str, FusedTargetEstimate], bool] | None = None,
        target_service_completed: Callable[[str, FusedTargetEstimate, bool], None] | None = None,
    ):
        require_sitl_command_adapter(adapter)
        self.adapter = adapter
        self.config = rescue_config
        self.flight_parameters = flight_parameters or FlightParameters()
        self.config.guided.approach_altitude_m = self.flight_parameters.approach_altitude_m
        self.config.guided.arrival_speed_m_s = self.flight_parameters.approach_speed_m_s
        self.config.delivery.delivery_altitude_m = self.flight_parameters.delivery_altitude_m
        self.config.delivery.safe_drop_offset_m = self.flight_parameters.safe_drop_offset_m
        self.config.delivery.payload_slots = self.flight_parameters.payload_slots
        self.readiness_timeout_s = readiness_timeout_s
        self.mission_timeout_s = mission_timeout_s
        self.rtl_timeout_s = rtl_timeout_s
        self.message_rates_hz = message_rates_hz
        self.telemetry_callback = telemetry_callback or (lambda _snapshot, _execution: None)
        self.event_callback = event_callback or (lambda _event: None)
        self.artifacts = artifacts
        self.payload_adapter = payload or SimulatedPayloadAdapter(
            latency_s=rescue_config.delivery.verification_latency_s
        )
        self.vehicle_id = vehicle_id
        self.landing_gate = landing_gate
        self.target_service_authorizer = target_service_authorizer
        self.target_service_completed = target_service_completed
        self.abort_requested = False
        self.mode_history: list[str] = []
        self.guided_trail: list[tuple[float, float]] = []
        self.search_trail: list[tuple[float, float]] = []
        self.resume_requested_seq: int | None = None
        self.resume_actual_seq: int | None = None
        self.delivery_evidence: Any | None = None
        self._target_lock = threading.Lock()
        self._live_targets_queue: list[FusedTargetEstimate] = []
        self._known_target_ids: set[str] = set()
        self._all_confirmed_targets: dict[str, FusedTargetEstimate] = {}
        self._active_mission_id: str | None = None

    def queue_target(self, target: FusedTargetEstimate) -> bool:
        """Queue a confirmed survivor target for sequential autonomous delivery."""
        with self._target_lock:
            # Check spatial deduplication against existing confirmed targets
            for existing_id, existing_target in self._all_confirmed_targets.items():
                if existing_id == target.target_id:
                    continue
                dist = math.hypot(target.east_m - existing_target.east_m, target.north_m - existing_target.north_m)
                if dist < 16.0:
                    logger.info(
                        "Target %s at (%.1f, %.1f) is a spatial duplicate of %s at (%.1f, %.1f) (dist=%.2fm). Ignoring duplicate.",
                        target.target_id, target.east_m, target.north_m,
                        existing_id, existing_target.east_m, existing_target.north_m, dist,
                    )
                    self._known_target_ids.add(target.target_id)
                    return False

            self._all_confirmed_targets[target.target_id] = target
            if target.target_id in self._known_target_ids:
                return False
            self._known_target_ids.add(target.target_id)
            self._live_targets_queue.append(target)
            return True

    def queue_target_data(self, data: dict[str, Any]) -> bool:
        """Queue target from dict representation, converting to FusedTargetEstimate."""
        try:
            target_id = data["target_id"]
            seen = data.get("first_seen_at")
            if isinstance(seen, str):
                first_seen = datetime.fromisoformat(seen)
            elif isinstance(seen, datetime):
                first_seen = seen
            else:
                first_seen = datetime.now(UTC)

            last = data.get("last_seen_at")
            if isinstance(last, str):
                last_seen = datetime.fromisoformat(last)
            elif isinstance(last, datetime):
                last_seen = last
            else:
                last_seen = datetime.now(UTC)

            from rescue_core.geolocation.models import GeolocationQuality
            quality_raw = str(data.get("quality") or data.get("geolocation_quality") or "STANDARD_GNSS_LIMITED")
            try:
                quality_val = GeolocationQuality(quality_raw)
            except Exception:
                quality_val = GeolocationQuality.STANDARD_GNSS_LIMITED

            estimate = FusedTargetEstimate(
                target_id=target_id,
                mission_id=data.get("mission_id", self._active_mission_id or ""),
                state=TargetTrackState.CONFIRMED,
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                east_m=float(data["east_m"]),
                north_m=float(data["north_m"]),
                uncertainty_m=float(data.get("geolocation_uncertainty_m", data.get("uncertainty_m", 1.0))),
                observation_count=int(data.get("observation_count", 4)),
                inlier_count=int(data.get("inlier_count", 4)),
                outlier_count=int(data.get("outlier_count", 0)),
                detection_confidence=float(data.get("detection_confidence", 0.5)),
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                assigned_vehicle_id=str(data.get("assigned_vehicle_id", self.vehicle_id)),
                quality=quality_val,
                simulated=bool(data.get("simulated", True)),
            )
            return self.queue_target(estimate)
        except Exception as exc:
            print(f"[RESCUE_RUNNER] Failed to parse target data: {exc}", flush=True)
            return False

    def execute(
        self, plan: CoveragePlan, boundary: Boundary,
        scenario: SyntheticSurvivorScenario | list[SyntheticSurvivorScenario] | None = None,
        git_commit: str | None = None,
    ) -> MissionExecution:
        if scenario is None:
            scenarios = []
            primary_scenario = None
        else:
            scenarios = [scenario] if isinstance(scenario, SyntheticSurvivorScenario) else list(scenario)
            if not scenarios or len({item.target_id for item in scenarios}) != len(scenarios):
                raise ValueError("SITL rescue scenarios require one or more unique target IDs")
            primary_scenario = scenarios[0]
        self._active_mission_id = plan.mission_id
        execution = MissionExecution(
            mission_id=plan.mission_id, source=ExecutionSource.SITL,
            state=ExecutionState.SITL_CONNECTING, connection=self.adapter.connection_string,
            ardupilot_git_commit=git_commit,
            metadata={"stage": "7S", "hardware_gate_h1": "PENDING"},
        )
        self._execution = execution
        mission = ArduCopterMissionBuilder().build(plan, include_rtl=self.landing_gate is None)
        execution.mission_fingerprint = mission.fingerprint
        execution.total_mission_items = len(mission.items)
        execution.metadata["coordinated_landing"] = self.landing_gate is not None
        tracker = ActualCoverageTracker(plan, boundary.origin)
        frame = LocalCoordinateFrame(boundary.origin)
        original_region = Polygon([
            (frame.to_enu(point).east_m, frame.to_enu(point).north_m) for point in boundary.vertices
        ])
        region = operational_flight_region(original_region, self.config.safety.operational_boundary_margin_m)
        inventory = PayloadInventory(self.config.delivery.payload_slots)
        eligibility = TargetEligibilityPolicy(
            region, self.config.target.max_geolocation_uncertainty_m,
            self.config.target.minimum_battery_percent, self.config.safety.delivery_time_reserve_s,
        )
        orchestrator = RescueMissionOrchestrator(
            eligibility, TargetServiceQueue(), transition_callback=self._transition,
        )
        metrics = RescueMetrics(planned_search_distance_m=plan.estimated_path_length_m)
        search_first = 3
        rtl_sequence = len(mission.items) - 1 if self.landing_gate is None else len(mission.items)
        final_search_sequence = len(mission.items) - 1
        resume = SearchResumeManager(search_first, rtl_sequence, self.config.resume.policy)
        confirmed_target = None
        delivered_ids: set[str] = set()
        deferred_ids: set[str] = set()
        delivery_attempts: dict[str, int] = {}
        mission_started_mono: float | None = None
        last_reached: set[int] = set()
        rtl_seen = False
        armed_seen = False
        try:
            orchestrator.transition(RescueEvent.PREPARE)
            identity = self.adapter.connect()
            self._event(plan.mission_id, EventType.SITL_CONNECTED, "Connected to guarded ArduCopter SITL", identity)
            self.adapter.pose_subscriber.set_origin(boundary.origin)
            self.adapter.request_message_rates(self.message_rates_hz)
            readiness = self.adapter.wait_ready(self.readiness_timeout_s)
            self._event(plan.mission_id, EventType.SITL_READY, "SITL health gate passed", readiness)
            firmware = self.adapter.firmware_version()
            execution.autopilot_version = firmware.get("version") if firmware else None
            execution.state = ExecutionState.MISSION_UPLOADING
            self._event(plan.mission_id, EventType.MISSION_UPLOAD_STARTED, "Stage 7S search mission upload started")
            transfer, verification = self.adapter.upload_and_verify(mission)
            if self.artifacts:
                self.artifacts.write_json("coverage_plan.json", plan)
                self.artifacts.write_json("mavlink_mission.json", mission)
                self.artifacts.write_json("mission_verification.json", verification)
                self.artifacts.write_json(
                    "synthetic_survivor_private.json",
                    [item.model_dump(mode="json") for item in scenarios],
                )
            self._event(plan.mission_id, EventType.MISSION_VERIFIED, "Uploaded mission downloaded and verified", {
                "items": transfer.item_count, "fingerprint": mission.fingerprint,
            })
            execution.state = ExecutionState.ARMING
            self.adapter.prepare_for_arm()
            self.adapter.arm()
            armed_seen = True
            orchestrator.transition(RescueEvent.MISSION_ARMED)
            self._event(plan.mission_id, EventType.VEHICLE_ARMED, "SITL vehicle armed")
            self.adapter.start_auto_mission()
            execution.started_at = datetime.now(UTC)
            mission_started_mono = time.monotonic()
            execution.state = ExecutionState.TAKING_OFF
            self._event(plan.mission_id, EventType.AUTO_STARTED, "Initial AUTO mission accepted")
            perceptions = {
                item.target_id: SyntheticSearchTargetSource(
                    self.adapter, plan.mission_id, frame, item, self.vehicle_id,
                )
                for item in scenarios
            }
            deadline = time.monotonic() + self.mission_timeout_s
            while time.monotonic() < deadline:
                if self.abort_requested:
                    orchestrator.transition(RescueEvent.ABORT)
                    self.adapter.rtl()
                    rtl_seen = True
                    break
                snapshot = self._next_snapshot(1.0)
                if snapshot is None:
                    self._assert_link()
                    continue
                self._record_snapshot(snapshot, execution, tracker, search_first, rtl_sequence, orchestrator.state)
                newly_reached = set(snapshot.reached_items) - last_reached
                for sequence in sorted(newly_reached):
                    execution.waypoints_reached.append(sequence)
                    self._event(plan.mission_id, EventType.WAYPOINT_REACHED, f"Mission item {sequence} reached", {"sequence": sequence})
                last_reached.update(newly_reached)
                if (
                    self.landing_gate is not None
                    and not rtl_seen
                    and final_search_sequence in snapshot.reached_items
                    and self.landing_gate.try_acquire(self.vehicle_id)
                ):
                    self.adapter.rtl()
                    rtl_seen = True
                    execution.state = ExecutionState.RETURNING
                    self._event(
                        plan.mission_id,
                        EventType.RTL_STARTED,
                        "Fleet landing slot acquired; coordinated RTL sent",
                        {"vehicle_id": self.vehicle_id},
                    )
                if orchestrator.state is RescueState.TAKEOFF and snapshot.vehicle.relative_altitude_m >= .5:
                    orchestrator.transition(RescueEvent.TAKEOFF_OBSERVED)
                    execution.state = ExecutionState.SEARCHING
                    self._event(plan.mission_id, EventType.TAKEOFF, "SITL takeoff observed")
                    self._event(plan.mission_id, EventType.SEARCH_STARTED, "AUTO coverage search started")
                searching_item = search_first <= snapshot.current_mission_item < rtl_sequence
                if searching_item and orchestrator.state in {RescueState.SEARCHING, RescueState.TARGET_CANDIDATE}:
                    # 1. Update any synthetic scenarios
                    for active_scenario in scenarios:
                        if active_scenario.target_id in delivered_ids or active_scenario.target_id in deferred_ids:
                            continue
                        perception = perceptions[active_scenario.target_id]
                        observation, estimate = perception.update()
                        if observation and self.artifacts:
                            self.artifacts.append_observation(observation)
                        if observation and metrics.time_to_first_detection_s is None and execution.started_at:
                            metrics.time_to_first_detection_s = max(
                                0, (observation.created_at - execution.started_at).total_seconds()
                            )
                        if observation and orchestrator.state is RescueState.SEARCHING:
                            orchestrator.transition(RescueEvent.CANDIDATE_OBSERVED)
                            self._event(plan.mission_id, EventType.TARGET_CANDIDATE, "Synthetic Stage 5 observation created", {
                                "frame_id": observation.frame_id,
                            }, target_id=active_scenario.target_id, simulated=True)
                        if estimate and estimate.state is TargetTrackState.CONFIRMED and active_scenario.target_id not in delivered_ids:
                            service_estimate = estimate
                            if active_scenario.coarse_estimate_offset_east_m or active_scenario.coarse_estimate_offset_north_m:
                                biased_east = estimate.east_m + active_scenario.coarse_estimate_offset_east_m
                                biased_north = estimate.north_m + active_scenario.coarse_estimate_offset_north_m
                                biased_wgs84 = frame.enu_to_wgs84(biased_east, biased_north, 0)
                                service_estimate = estimate.model_copy(update={
                                    "east_m": biased_east, "north_m": biased_north,
                                    "latitude": biased_wgs84.latitude, "longitude": biased_wgs84.longitude,
                                })
                            if perception.first_detection_at and perception.confirmation_at:
                                metrics.detection_to_confirmation_s = max(
                                    0, (perception.confirmation_at - perception.first_detection_at).total_seconds()
                                )
                            self.queue_target(service_estimate)

                    # 2. Sequential multi-target servicing loop
                    if inventory.remaining > 0:
                        queued_target = None
                        with self._target_lock:
                            for candidate in list(self._live_targets_queue):
                                if candidate.target_id not in delivered_ids and candidate.target_id not in deferred_ids:
                                    delivered_targets = [
                                        self._all_confirmed_targets[tid] for tid in delivered_ids
                                        if tid in self._all_confirmed_targets
                                    ]
                                    is_dup = False
                                    for dt in delivered_targets:
                                        dist = math.hypot(candidate.east_m - dt.east_m, candidate.north_m - dt.north_m)
                                        if dist < 16.0:
                                            is_dup = True
                                            delivered_ids.add(candidate.target_id)
                                            logger.info(
                                                "Candidate %s is within %.2fm of delivered %s. Marking as duplicate.",
                                                candidate.target_id, dist, dt.target_id,
                                            )
                                            break
                                    if is_dup:
                                        self._live_targets_queue.remove(candidate)
                                        continue
                                    queued_target = candidate
                                    self._live_targets_queue.remove(candidate)
                                    break

                        if queued_target is not None:
                            confirmed_target = queued_target
                            confirmed_monotonic = time.monotonic()
                            if orchestrator.state in {RescueState.SEARCHING, RescueState.TARGET_CANDIDATE}:
                                orchestrator.transition(RescueEvent.TARGET_CONFIRMED)
                            self._event(plan.mission_id, EventType.TARGET_CONFIRMED, "Target confirmed and queued for delivery", {
                                "target": queued_target.model_dump(mode="json"),
                            }, target_id=queued_target.target_id)
                            self._event(plan.mission_id, EventType.DELIVERY_QUEUED, "Survivor queued for autonomous delivery", {
                                "target_id": queued_target.target_id,
                                "inventory_remaining": inventory.remaining,
                            }, target_id=queued_target.target_id)

                            remaining = self.config.safety.maximum_mission_duration_s - (time.monotonic() - mission_started_mono)
                            decision = orchestrator.consider_target(
                                queued_target, inventory, delivery_required=True,
                                vehicle_healthy=True, battery_percent=snapshot.vehicle.battery_percent,
                                remaining_mission_time_s=remaining,
                            )
                            if not decision.eligible:
                                self._event(plan.mission_id, EventType.TARGET_UNSERVICEABLE, decision.reason, target_id=queued_target.target_id)
                                if str(decision.reason) != "INSUFFICIENT_SURVIVOR_EVIDENCE":
                                    deferred_ids.add(queued_target.target_id)
                                else:
                                    with self._target_lock:
                                        self._known_target_ids.discard(queued_target.target_id)
                                orchestrator.transition(RescueEvent.TARGET_DEFERRED)
                                continue

                            if self.target_service_authorizer is not None and not self.target_service_authorizer(
                                self.vehicle_id, queued_target
                            ):
                                self._event(
                                    plan.mission_id,
                                    EventType.TARGET_UNSERVICEABLE,
                                    "Fleet delivery assignment belongs to another vehicle",
                                    target_id=queued_target.target_id,
                                )
                                deferred_ids.add(queued_target.target_id)
                                orchestrator.transition(RescueEvent.TARGET_DEFERRED)
                                continue

                            # Save search resume checkpoint before diverting
                            checkpoint = resume.checkpoint(
                                mission_id=plan.mission_id, mission_fingerprint=mission.fingerprint,
                                current_seq=snapshot.current_mission_item,
                                reached_items=snapshot.reached_items,
                                east_m=self._position()[0], north_m=self._position()[1],
                                coverage_percent=execution.actual_coverage_percent,
                            )
                            orchestrator.save_checkpoint(checkpoint)
                            self._event(plan.mission_id, EventType.SEARCH_CHECKPOINT_SAVED, "Search resume checkpoint persisted", checkpoint.model_dump(mode="json"))

                            # Service queued targets sequentially
                            active_target = queued_target
                            while active_target is not None:
                                orchestrator.select_next_target()
                                matched_scenario = next((s for s in scenarios if s.target_id == active_target.target_id), None)
                                other_survivors = [
                                    (t.east_m, t.north_m) for t in self._all_confirmed_targets.values()
                                    if t.target_id != active_target.target_id
                                ]
                                try:
                                    delivered = self._service_target(
                                        plan, execution, orchestrator, active_target, matched_scenario, inventory,
                                        region, resume, tracker, frame, metrics, confirmed_monotonic, other_survivors,
                                    )
                                except RescueAbortRequested:
                                    orchestrator.transition(RescueEvent.ABORT)
                                    self.adapter.rtl()
                                    rtl_seen = True
                                    execution.state = ExecutionState.RETURNING
                                    self._event(
                                        plan.mission_id, EventType.RTL_STARTED,
                                        "Operator/safety recall interrupted GUIDED; drop gate closed and RTL confirmed",
                                        {"payload_released": False}, severity=EventSeverity.WARNING,
                                    )
                                    break
                                except Exception as service_err:
                                    logger.error("Target servicing exception for %s: %s", active_target.target_id, service_err, exc_info=True)
                                    print(f"Target servicing exception for {active_target.target_id}: {type(service_err).__name__}: {service_err}", flush=True)
                                    self._event(
                                        plan.mission_id, EventType.DELIVERY_FAILED,
                                        f"Target servicing exception: {type(service_err).__name__}: {service_err}",
                                        {"target_id": active_target.target_id, "error": str(service_err)},
                                        target_id=active_target.target_id, severity=EventSeverity.WARNING,
                                    )
                                    if orchestrator.state not in (RescueState.SEARCHING, RescueState.SEARCH_RESUMING):
                                        try:
                                            orchestrator.transition(RescueEvent.DELIVERY_FAILED)
                                            orchestrator.service_finished(False)
                                        except Exception:
                                            pass
                                    delivered = False

                                if delivered:
                                    delivered_ids.add(active_target.target_id)
                                else:
                                    delivery_attempts[active_target.target_id] = delivery_attempts.get(active_target.target_id, 0) + 1
                                    if delivery_attempts[active_target.target_id] >= 2:
                                        deferred_ids.add(active_target.target_id)
                                if self.target_service_completed is not None:
                                    self.target_service_completed(self.vehicle_id, active_target, delivered)

                                # Check for next target in queue
                                active_target = None
                                if inventory.remaining > 0:
                                    with self._target_lock:
                                        for cand in list(self._live_targets_queue):
                                            if cand.target_id not in delivered_ids and cand.target_id not in deferred_ids:
                                                delivered_targets = [
                                                    self._all_confirmed_targets[tid] for tid in delivered_ids
                                                    if tid in self._all_confirmed_targets
                                                ]
                                                is_dup = False
                                                for dt in delivered_targets:
                                                    dist = math.hypot(cand.east_m - dt.east_m, cand.north_m - dt.north_m)
                                                    if dist < 16.0:
                                                        is_dup = True
                                                        delivered_ids.add(cand.target_id)
                                                        logger.info(
                                                            "Candidate %s is within %.2fm of delivered %s. Marking as duplicate.",
                                                            cand.target_id, dist, dt.target_id,
                                                        )
                                                        break
                                                if is_dup:
                                                    self._live_targets_queue.remove(cand)
                                                    continue
                                                active_target = cand
                                                self._live_targets_queue.remove(cand)
                                                break

                            if self.abort_requested or rtl_seen:
                                break

                            # All queued targets handled; safely resume search at saved checkpoint
                            self.adapter.set_speed(self.flight_parameters.search_speed_m_s)
                            resume_started = time.monotonic()
                            navigator = SITLGuidedNavigator(self.adapter, frame)
                            self._resume_search(plan, execution, orchestrator, navigator, resume)
                            metrics.resume_time_s = time.monotonic() - resume_started
                            continue

                if snapshot.current_mission_item >= rtl_sequence or snapshot.vehicle.flight_mode == "RTL":
                    first_rtl_evidence = not rtl_seen
                    if not rtl_seen and self._actual_mode() != "RTL":
                        # Set configured RTL parameters on vehicle
                        try:
                            self.adapter.set_parameter("RTL_ALT", int(self.flight_parameters.rtl_altitude_m * 100))
                            self.adapter.set_speed(self.flight_parameters.rtl_speed_m_s)
                        except Exception:
                            pass
                        self.adapter.rtl()
                    if self._actual_mode() == "RTL" and (not self.mode_history or self.mode_history[-1] != "RTL"):
                        self.mode_history.append("RTL")
                    if orchestrator.state is RescueState.SEARCHING:
                        orchestrator.transition(RescueEvent.RTL_OBSERVED)
                    execution.state = ExecutionState.RETURNING
                    rtl_seen = True
                    if first_rtl_evidence:
                        self._event(plan.mission_id, EventType.RTL_STARTED, "Final onboard RTL observed")
                is_on_ground = snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
                is_stuck_landing = (
                    snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_LANDING
                    and abs(snapshot.vehicle.vertical_speed_m_s) < 0.1
                    and snapshot.vehicle.relative_altitude_m < 2.0
                )
                landed = is_on_ground or is_stuck_landing
                if rtl_seen and landed:
                    if snapshot.vehicle.armed:
                        try:
                            self.adapter.force_disarm_grounded_simulation(grounded_evidence=landed)
                        except Exception as disarm_err:
                            logger.warning("force_disarm notice: %s", disarm_err)
                    if orchestrator.state is RescueState.RETURNING:
                        orchestrator.transition(RescueEvent.LANDING_OBSERVED)
                    execution.state = ExecutionState.LANDING
                    execution.rtl_completed = True
                    execution.landing_detected = True
                if rtl_seen and landed and armed_seen and not snapshot.vehicle.armed:
                    orchestrator.transition(RescueEvent.DISARMED)
                    execution.state = ExecutionState.COMPLETED
                    execution.disarm_detected = True
                    execution.completed_at = datetime.now(UTC)
                    self._event(plan.mission_id, EventType.VEHICLE_LANDED, "SITL landing detected")
                    self._event(plan.mission_id, EventType.VEHICLE_DISARMED, "SITL disarm detected")
                    self._event(plan.mission_id, EventType.MISSION_COMPLETED, "Stage 7S rescue mission completed")
                    if self.landing_gate is not None:
                        self.landing_gate.complete(self.vehicle_id)
                    break
            else:
                raise TimeoutError("Stage 7S mission deadline exceeded")
            if self.abort_requested:
                self._monitor_abort_landing(execution, orchestrator)
            if execution.state is not ExecutionState.COMPLETED and not self.abort_requested:
                raise RescueMissionError("Stage 7S execution ended without landing/disarm evidence")
        except Exception as exc:
            execution.errors.append(f"{type(exc).__name__}: {exc}")
            execution.state = ExecutionState.FAILED
            execution.completed_at = datetime.now(UTC)
            try:
                orchestrator.transition(RescueEvent.FAIL)
            except Exception:
                pass
            self._event(plan.mission_id, EventType.FAILSAFE, str(exc), severity=EventSeverity.ERROR)
            if armed_seen and not rtl_seen:
                try:
                    self.adapter.rtl()
                    self._event(plan.mission_id, EventType.RTL_STARTED, "Failure recovery RTL requested", severity=EventSeverity.WARNING)
                except Exception as recovery:
                    execution.errors.append(f"RTL recovery failed: {recovery}")
        finally:
            coverage = tracker.metrics()
            for key, value in coverage.items():
                setattr(execution, key, value)
            metrics.actual_search_distance_m = self._length(self.search_trail)
            metrics.guided_diversion_distance_m = self._length(self.guided_trail)
            metrics.total_distance_m = execution.actual_path_length_m
            metrics.final_search_coverage_percent = execution.actual_coverage_percent
            execution.metadata.update({
                "hardware_gate_h1": "PENDING",
                "rescue_state": orchestrator.state,
                "mode_history": self.mode_history,
                "target": confirmed_target.model_dump(mode="json") if confirmed_target else None,
                "target_scenarios": [item.model_dump(mode="json") for item in scenarios],
                "delivered_target_ids": sorted(delivered_ids),
                "deferred_target_ids": sorted(deferred_ids),
                "target_source": "STAGE5_SYNTHETIC_PROJECTION_AND_CONFIRMATION",
                "payload": self.delivery_evidence.model_dump(mode="json") if self.delivery_evidence else None,
                "payload_inventory_remaining": inventory.remaining,
                "resume_policy": self.config.resume.policy,
                "resume_requested_seq": self.resume_requested_seq,
                "resume_actual_seq": self.resume_actual_seq,
                "search_resume_checkpoint": orchestrator.checkpoint.model_dump(mode="json") if orchestrator.checkpoint else None,
                "metrics": metrics.model_dump(mode="json"),
                "guided_trail_enu": self.guided_trail,
                "search_trail_enu": self.search_trail,
            })
            execution.updated_at = datetime.now(UTC)
            self.adapter.disconnect()
            if self.artifacts:
                self._finish_artifacts(execution, plan, scenario)
        return execution

    def _service_target(
        self, plan: CoveragePlan, execution: MissionExecution,
        orchestrator: RescueMissionOrchestrator, target: Any,
        scenario: SyntheticSurvivorScenario | None, inventory: PayloadInventory,
        region: Polygon, resume: SearchResumeManager, coverage: ActualCoverageTracker,
        frame: LocalCoordinateFrame, metrics: RescueMetrics, confirmed_monotonic: float,
        other_survivors: list[tuple[float, float]] | None = None,
    ) -> bool:
        start = time.monotonic()
        execution.state = ExecutionState.DELIVERING
        east, north, _ = self._position()

        # Compute safe standoff delivery point: NEVER drop directly on person's body!
        safe_point = compute_safe_delivery_point(
            target_east_m=target.east_m,
            target_north_m=target.north_m,
            drone_east_m=east,
            drone_north_m=north,
            safe_offset_m=self.flight_parameters.safe_drop_offset_m,
            operational_region=region,
            other_survivors=other_survivors,
            target_uncertainty_m=getattr(target, "uncertainty_m", 0.0),
        )
        if safe_point is None:
            self._event(
                plan.mission_id, EventType.DELIVERY_FAILED,
                "No safe standoff delivery point could be established inside boundary",
                target_id=target.target_id, severity=EventSeverity.WARNING,
            )
            orchestrator.transition(RescueEvent.DELIVERY_FAILED)
            orchestrator.service_finished(False)
            return False

        deliv_east, deliv_north = safe_point
        try:
            approach = GuidedApproachPlanner(region, tolerance_m=5.0).plan(
                east, north, deliv_east, deliv_north, self.flight_parameters.approach_altitude_m
            )
        except Exception as plan_err:
            logger.warning("Approach path planning failed for target %s: %s", target.target_id, plan_err)
            self._event(
                plan.mission_id, EventType.DELIVERY_FAILED,
                f"Approach path planning failed: {plan_err}",
                target_id=target.target_id, severity=EventSeverity.WARNING,
            )
            orchestrator.transition(RescueEvent.DELIVERY_FAILED)
            orchestrator.service_finished(False)
            return False
        orchestrator.transition(RescueEvent.APPROACH_PATH_READY)
        self._event(plan.mission_id, EventType.APPROACH_STARTED, "Safe internal GUIDED path to standoff delivery point planned", {
            "path_length_m": approach.path_length_m, "points": [p.model_dump() for p in approach.points],
            "delivery_point_enu": [deliv_east, deliv_north],
            "target_enu": [target.east_m, target.north_m],
            "standoff_m": math.hypot(deliv_east - target.east_m, deliv_north - target.north_m),
            "path_wgs84": [
                frame.enu_to_wgs84(point.east_m, point.north_m, point.altitude_m).model_dump(mode="json")
                for point in approach.points
            ],
        }, target_id=target.target_id)
        navigator = SITLGuidedNavigator(self.adapter, frame)
        self._event(plan.mission_id, EventType.GUIDED_REQUESTED, "GUIDED mode requested", target_id=target.target_id)
        navigator.enter_guided()
        if not self.mode_history or self.mode_history[-1] != "GUIDED":
            self.mode_history.append("GUIDED")
        metrics.confirmation_to_guided_s = time.monotonic() - confirmed_monotonic
        orchestrator.transition(RescueEvent.GUIDED_CONFIRMED)
        self._event(plan.mission_id, EventType.GUIDED_ENTERED, "Actual HEARTBEAT confirms GUIDED", target_id=target.target_id)
        self._assert_not_aborting(navigator)
        self.adapter.set_speed(self.flight_parameters.approach_speed_m_s)
        supervisor = GuidedSafetySupervisor(
            region, self.config.safety.guided_command_timeout_s,
            maximum_altitude_m=max(45.0, self.flight_parameters.search_altitude_m + 15.0),
            buffer_m=18.0,
        )
        for point in approach.points:
            self._wait_arrival(navigator, point, supervisor, inventory, self.config.guided.max_approach_time_s)
        metrics.target_approach_time_s = time.monotonic() - start
        orchestrator.transition(RescueEvent.APPROACH_REACHED)
        self._event(plan.mission_id, EventType.APPROACH_REACHED, "Actual SITL position reached standoff delivery area", target_id=target.target_id)

        # Alignment and stabilization at delivery location
        if scenario is not None:
            provider = SyntheticReacquisitionProvider(
                scenario, self.config.reacquisition.search_radius_m,
            )
            self._event(plan.mission_id, EventType.REACQUISITION_STARTED, "Synthetic close-range reacquisition started", target_id=target.target_id, simulated=True)
            reacquisition_started = time.monotonic()
            observation = provider.observe(*self._position()[:2])
            if not observation.visible:
                search = ReacquisitionSearchPlanner(region, self.config.reacquisition.search_radius_m)
                for point in search.plan(*self._position()[:2]):
                    self._wait_arrival(
                        navigator,
                        ApproachPoint(east_m=point.east_m, north_m=point.north_m, altitude_m=self.flight_parameters.approach_altitude_m),
                        supervisor, inventory, min(15, self.config.reacquisition.timeout_s),
                    )
                    observation = provider.observe(*self._position()[:2])
                    if observation.visible:
                        break
            if not observation.visible:
                self._event(plan.mission_id, EventType.REACQUISITION_FAILED, "Target not visible in bounded search", target_id=target.target_id, severity=EventSeverity.WARNING)
                orchestrator.transition(RescueEvent.DELIVERY_FAILED)
                orchestrator.service_finished(False)
                return False
            orchestrator.transition(RescueEvent.TARGET_REACQUIRED)
            metrics.reacquisition_time_s = time.monotonic() - reacquisition_started
            self._event(plan.mission_id, EventType.TARGET_REACQUIRED, "Synthetic target reacquired", target_id=target.target_id, simulated=True)
            self._event(plan.mission_id, EventType.ALIGNMENT_STARTED, "Precision horizontal alignment started", target_id=target.target_id)
            controller = PrecisionAlignmentController(
                self.config.alignment.kp, self.config.alignment.max_horizontal_speed_m_s,
                self.config.alignment.deadband_m, self.config.alignment.alignment_radius_m,
                self.config.alignment.observation_stale_s,
            )
            alignment_started = time.monotonic()
            stable_since: float | None = None
            initial_error = math.hypot(observation.relative_east_m or 0, observation.relative_north_m or 0)
            while time.monotonic() - alignment_started < self.config.reacquisition.timeout_s:
                self._assert_not_aborting(navigator)
                observation = provider.observe(*self._position()[:2])
                if not observation.visible:
                    navigator.hold()
                    self._event(plan.mission_id, EventType.REACQUISITION_FAILED, "Target lost during alignment; no drop", target_id=target.target_id, simulated=True, severity=EventSeverity.WARNING)
                    orchestrator.transition(RescueEvent.DELIVERY_FAILED)
                    orchestrator.service_finished(False)
                    return False
                command = controller.command(observation)
                navigator.set_velocity_local(command.velocity_east_m_s, command.velocity_north_m_s)
                supervisor.record_command()
                self._consume_guided_snapshot(execution, coverage)
                self._check_guided_safety(supervisor, inventory)
                if command.horizontal_error_m <= self.config.alignment.alignment_radius_m:
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= self.config.alignment.stable_dwell_s:
                        break
                else:
                    stable_since = None
                time.sleep(1 / self.config.guided.setpoint_rate_hz)
            orchestrator.transition(RescueEvent.ALIGNMENT_CONVERGED)
            metrics.alignment_time_s = time.monotonic() - alignment_started
            self._event(plan.mission_id, EventType.ALIGNMENT_COMPLETE, "Synthetic relative alignment converged", {
                "initial_error_m": initial_error,
                "final_error_m": math.hypot(observation.relative_east_m or 0, observation.relative_north_m or 0),
                "convergence_time_s": time.monotonic() - alignment_started,
            }, target_id=target.target_id, simulated=True)
        else:
            orchestrator.transition(RescueEvent.TARGET_REACQUIRED)
            self._event(plan.mission_id, EventType.TARGET_REACQUIRED, "Live target location confirmed for delivery", target_id=target.target_id)
            self._event(plan.mission_id, EventType.ALIGNMENT_STARTED, "Aligning at standoff delivery position", target_id=target.target_id)
            orchestrator.transition(RescueEvent.ALIGNMENT_CONVERGED)
            self._event(plan.mission_id, EventType.ALIGNMENT_COMPLETE, "Alignment converged at safe standoff", {
                "delivery_point_enu": [deliv_east, deliv_north],
            }, target_id=target.target_id)

        # Controlled descent to configured delivery altitude at safe standoff point
        delivery_point = ApproachPoint(
            east_m=deliv_east,
            north_m=deliv_north,
            altitude_m=self.flight_parameters.delivery_altitude_m,
        )
        self._wait_arrival(navigator, delivery_point, supervisor, inventory, 75.0)

        # Stabilization hold at delivery point
        stabilization_deadline = time.monotonic() + self.flight_parameters.delivery_stabilization_s
        while time.monotonic() < stabilization_deadline:
            self._assert_not_aborting(navigator)
            navigator.goto_local(delivery_point)
            supervisor.record_command()
            self._consume_guided_snapshot(execution, coverage)
            self._check_guided_safety(supervisor, inventory)
            time.sleep(1 / self.config.guided.setpoint_rate_hz)
        navigator.hold()

        self._assert_not_aborting(navigator)
        orchestrator.transition(RescueEvent.DROP_CONDITIONS_MET)
        delivery_started = time.monotonic()
        slot = inventory.reserve(target.target_id)
        self._event(plan.mission_id, EventType.RELEASE_AUTHORIZED, "All release gates passed; simulated release authorized", {
            "target_id": target.target_id, "payload_slot": slot.slot_id,
        }, target_id=target.target_id)
        self._event(plan.mission_id, EventType.DELIVERY_RELEASED, "Simulated payload release requested", target_id=target.target_id, simulated=True)
        east, north, altitude = self._position()
        rel_error = math.hypot(east - deliv_east, north - deliv_north)
        latest_vehicle = self.adapter.vehicle_source.normalizer.snapshot.vehicle
        delivery = self.payload_adapter.release(
            slot, vehicle_east_m=east, vehicle_north_m=north,
            relative_target_error_m=rel_error, altitude_m=altitude,
            ground_speed_m_s=latest_vehicle.ground_speed_m_s, target_uncertainty_m=getattr(target, "uncertainty_m", 0.0),
        )
        self.delivery_evidence = delivery
        orchestrator.transition(RescueEvent.RELEASE_ACCEPTED)
        self._event(plan.mission_id, EventType.DELIVERY_RELEASED, "Simulated payload state is RELEASED", {
            "delivery_point_enu": [deliv_east, deliv_north],
            "target_id": target.target_id,
            "payload_slot": slot.slot_id,
            "release_altitude_m": altitude,
        }, target_id=target.target_id, simulated=True)

        if self.payload_adapter.verify(slot, delivery):
            orchestrator.transition(RescueEvent.DELIVERY_VERIFIED)
            self._event(plan.mission_id, EventType.DELIVERY_VERIFIED, "Simulated departure verification succeeded", {
                "delivery_point_enu": [deliv_east, deliv_north],
                "target_id": target.target_id,
                "payload_slot": slot.slot_id,
                "verified": True,
            }, target_id=target.target_id, simulated=True)
            orchestrator.service_finished(True)
            delivery_ok = True
        else:
            orchestrator.transition(RescueEvent.DELIVERY_FAILED)
            self._event(plan.mission_id, EventType.DELIVERY_FAILED, "Simulated payload verification failed", target_id=target.target_id, simulated=True, severity=EventSeverity.WARNING)
            orchestrator.service_finished(False)
            delivery_ok = False

        metrics.delivery_time_s = time.monotonic() - delivery_started

        # Safely climb back to approach altitude before any next maneuver
        climb_point = ApproachPoint(east_m=deliv_east, north_m=deliv_north, altitude_m=self.flight_parameters.approach_altitude_m)
        climb_deadline = time.monotonic() + 20
        while self._position()[2] < self.flight_parameters.approach_altitude_m - 0.5:
            self._assert_not_aborting(navigator)
            if time.monotonic() > climb_deadline:
                break
            navigator.goto_local(climb_point)
            supervisor.record_command()
            self._consume_guided_snapshot(execution, coverage)
            time.sleep(0.5)

        metrics.service_total_time_s = time.monotonic() - start
        metrics.search_interruption_overhead_s = metrics.service_total_time_s
        return delivery_ok

    def _resume_search(
        self, plan: CoveragePlan, execution: MissionExecution, orchestrator: RescueMissionOrchestrator,
        navigator: SITLGuidedNavigator, resume: SearchResumeManager,
    ) -> None:
        if not orchestrator.checkpoint:
            raise RescueMissionError("Cannot resume without persisted checkpoint")
        navigator.hold()

        # Safely climb vertically from delivery standoff location directly back to search altitude
        search_alt = self.flight_parameters.search_altitude_m
        current_pos = self._position()
        climb_point = ApproachPoint(east_m=current_pos[0], north_m=current_pos[1], altitude_m=search_alt)
        navigator.set_speed(self.flight_parameters.search_speed_m_s)
        navigator.goto_local(climb_point)

        arrival_deadline = time.monotonic() + 25.0
        while time.monotonic() < arrival_deadline:
            pos = self._position()
            if pos[2] >= search_alt - 1.0:
                break
            time.sleep(0.4)

        self.resume_requested_seq = resume.select_resume_sequence(orchestrator.checkpoint)
        self._event(plan.mission_id, EventType.SEARCH_RESUME_REQUESTED, "Forward search resumption requested", {
            "policy": self.config.resume.policy, "sequence": self.resume_requested_seq,
            "mis_restart": self.adapter.parameter_value("MIS_RESTART"),
            "resume_east_m": current_pos[0], "resume_north_m": current_pos[1],
            "cursor_east_m": orchestrator.checkpoint.vehicle_east_m, "cursor_north_m": orchestrator.checkpoint.vehicle_north_m,
        })
        navigator.set_mission_current(self.resume_requested_seq)
        navigator.resume_auto()
        current = self.adapter.router.wait_for(
            "MISSION_CURRENT", self.adapter.command_timeout_s,
            lambda item: int(item.seq) >= self.resume_requested_seq,
        )
        if current is None:
            raise RescueMissionError("AUTO resumed but mission-current evidence did not arrive")
        self.resume_actual_seq = int(current.seq)
        orchestrator.transition(RescueEvent.AUTO_CONFIRMED)
        execution.state = ExecutionState.SEARCHING
        self._event(plan.mission_id, EventType.SEARCH_RESUMED, "Actual HEARTBEAT and MISSION_CURRENT confirm AUTO resume", {
            "requested_sequence": self.resume_requested_seq, "actual_sequence": self.resume_actual_seq,
            "resume_east_m": current_pos[0], "resume_north_m": current_pos[1],
            "cursor_east_m": orchestrator.checkpoint.vehicle_east_m, "cursor_north_m": orchestrator.checkpoint.vehicle_north_m,
            "coverage_percent_pre_resume": orchestrator.checkpoint.coverage_percent,
        })

    def _wait_arrival(
        self, navigator: SITLGuidedNavigator, point: ApproachPoint,
        supervisor: GuidedSafetySupervisor, inventory: PayloadInventory, timeout_s: float,
    ) -> None:
        deadline, stable_since, last_send = time.monotonic() + timeout_s, None, 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            self._assert_not_aborting(navigator)
            if now - last_send >= .25:
                navigator.goto_local(point)
                supervisor.record_command(now)
                last_send = now
            snapshot = self._next_snapshot(.5)
            if snapshot is None:
                self._assert_link()
                continue
            east, north, altitude = self._position()
            self._append_distinct(self.guided_trail, (east, north))
            failures = supervisor.check(GuidedSafetySnapshot(
                link_alive=True, armed=self._actual_armed(), flight_mode=self._actual_mode(),
                east_m=east, north_m=north, altitude_m=altitude, payload_valid=inventory.remaining > 0,
            ), time.monotonic())
            if failures:
                raise RescueMissionError(
                    "GUIDED safety supervisor: " + ",".join(failures)
                    + f" at ENU=({east:.3f},{north:.3f},{altitude:.3f})"
                )
            horizontal = math.hypot(point.east_m - east, point.north_m - north)
            arrived = (
                horizontal <= self.config.guided.arrival_radius_m
                and abs(point.altitude_m - altitude) <= self.config.guided.arrival_altitude_tolerance_m
                and snapshot.vehicle.ground_speed_m_s <= self.config.guided.arrival_speed_m_s
            )
            if arrived:
                stable_since = stable_since or now
                if now - stable_since >= self.config.guided.arrival_dwell_s:
                    return
            else:
                stable_since = None
        raise RescueMissionError("GUIDED approach arrival timeout")

    def _consume_guided_snapshot(self, execution: MissionExecution, coverage: ActualCoverageTracker) -> None:
        snapshot = self._next_snapshot(.5)
        if snapshot:
            self._record_snapshot(snapshot, execution, coverage, 3, execution.total_mission_items - 1, RescueState.GUIDED_APPROACH)
            self._append_distinct(self.guided_trail, self._position()[:2])
        else:
            self._assert_link()

    def _check_guided_safety(self, supervisor: GuidedSafetySupervisor, inventory: PayloadInventory) -> None:
        east, north, altitude = self._position()
        latest = self.adapter.vehicle_source.normalizer.snapshot.vehicle
        failures = supervisor.check(GuidedSafetySnapshot(
            link_alive=True, armed=self._actual_armed(), flight_mode=self._actual_mode(),
            east_m=east, north_m=north, altitude_m=altitude,
            payload_valid=inventory.remaining > 0,
        ), time.monotonic())
        if failures:
            raise RescueMissionError("GUIDED safety supervisor: " + ",".join(failures))

    def _record_snapshot(
        self, snapshot: SITLTelemetrySnapshot, execution: MissionExecution,
        tracker: ActualCoverageTracker, search_first: int, rtl_seq: int, rescue_state: RescueState,
    ) -> None:
        execution.current_mission_item = snapshot.current_mission_item
        # Queue entries may predate a mode transition; mode evidence always comes
        # from the router's newest HEARTBEAT, which the adapter also verifies.
        mode = self._actual_mode()
        if mode and (not self.mode_history or self.mode_history[-1] != mode):
            self.mode_history.append(mode)
        searching = mode == "AUTO" and search_first <= snapshot.current_mission_item < rtl_seq and rescue_state in {
            RescueState.SEARCHING, RescueState.TARGET_CANDIDATE,
        }
        point = GeoPoint(
            latitude=snapshot.vehicle.latitude, longitude=snapshot.vehicle.longitude,
            altitude_m=snapshot.vehicle.relative_altitude_m,
        )
        if point.latitude or point.longitude:
            tracker.add(point, searching)
            execution.actual_trail = tracker.trail
            if searching:
                self._append_distinct(self.search_trail, self._position()[:2])
            for key, value in tracker.metrics().items():
                setattr(execution, key, value)
        execution.updated_at = datetime.now(UTC)
        if self.artifacts:
            self.artifacts.append_telemetry({
                **snapshot.model_dump(mode="json"), "rescue_state": rescue_state,
            })
        try:
            self.telemetry_callback(snapshot, execution)
        except Exception as _cb_err:
            pass

    def _position(self) -> tuple[float, float, float]:
        subscriber = self.adapter.pose_subscriber
        if not subscriber or not subscriber.history.samples:
            raise RescueMissionError("Local SITL pose unavailable")
        sample = subscriber.history.samples[-1]
        if not sample.local_position_ned_m:
            raise RescueMissionError("LOCAL_POSITION_NED unavailable")
        north, east, down = sample.local_position_ned_m
        return east, north, -down

    def _next_snapshot(self, timeout_s: float) -> SITLTelemetrySnapshot | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            source = self.adapter.vehicle_source
            snapshot = source.next_update() if source else None
            if snapshot is not None:
                return snapshot
            time.sleep(.01)
        return None

    def _assert_link(self) -> None:
        router = self.adapter.router
        if not router or router.last_message_monotonic is None or time.monotonic() - router.last_message_monotonic > self.adapter.heartbeat_timeout_s:
            raise TimeoutError("SITL MAVLink stream is stale")

    def _actual_mode(self) -> str:
        heartbeat = self.adapter.router.latest("HEARTBEAT") if self.adapter.router else None
        return mavutil.mode_string_v10(heartbeat) if heartbeat is not None else "UNKNOWN"

    def _actual_armed(self) -> bool:
        heartbeat = self.adapter.router.latest("HEARTBEAT") if self.adapter.router else None
        return bool(heartbeat and int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

    def _monitor_abort_landing(self, execution: MissionExecution, orchestrator: RescueMissionOrchestrator) -> None:
        deadline = time.monotonic() + self.rtl_timeout_s
        while time.monotonic() < deadline:
            snapshot = self._next_snapshot(1)
            if not snapshot:
                continue
            mode = self._actual_mode()
            if mode and (not self.mode_history or self.mode_history[-1] != mode):
                self.mode_history.append(mode)
            if self.artifacts:
                self.artifacts.append_telemetry({
                    **snapshot.model_dump(mode="json"), "rescue_state": orchestrator.state,
                    "abort_monitor": True,
                })
            is_on_ground = snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
            is_stuck_landing = (
                snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_LANDING
                and abs(snapshot.vehicle.vertical_speed_m_s) < 0.1
                and snapshot.vehicle.relative_altitude_m < 2.0
            )
            landed = is_on_ground or is_stuck_landing
            if landed and snapshot.vehicle.armed:
                self.adapter.force_disarm_grounded_simulation(grounded_evidence=is_stuck_landing)
            if landed and not snapshot.vehicle.armed:
                orchestrator.transition(RescueEvent.ABORTED)
                execution.state = ExecutionState.ABORTED
                execution.rtl_completed = mode in {"RTL", "LAND"} or "RTL" in self.mode_history
                execution.landing_detected = execution.disarm_detected = True
                execution.completed_at = datetime.now(UTC)
                self._event(execution.mission_id, EventType.VEHICLE_LANDED, "Abort/recall landing detected", severity=EventSeverity.WARNING)
                self._event(execution.mission_id, EventType.VEHICLE_DISARMED, "Abort/recall disarm detected", severity=EventSeverity.WARNING)
                self._event(execution.mission_id, EventType.MISSION_ABORTED, "Stage 8S safety recall ended in landed/disarmed state", severity=EventSeverity.WARNING)
                return
        raise TimeoutError("Abort RTL landing timeout")

    def abort(self) -> None:
        self.abort_requested = True

    def _assert_not_aborting(self, navigator: SITLGuidedNavigator) -> None:
        if not self.abort_requested:
            return
        # Stop refreshing movement setpoints before the outer loop requests RTL.
        # Payload state is untouched, so no release can be inferred or repeated.
        navigator.hold()
        raise RescueAbortRequested("SITL rescue recall requested")

    def _transition(self, transition: RescueTransitionRecord) -> None:
        if hasattr(self, "_execution"):
            self._execution.metadata["rescue_state"] = transition.current
        self._event(
            "unknown" if not hasattr(self, "_active_mission_id") else self._active_mission_id,
            EventType.RESCUE_STATE_CHANGED,
            f"{transition.prior} -> {transition.current}",
            {
                "event": transition.event, "prior_state": transition.prior,
                "current_state": transition.current, **transition.metadata,
            },
        )

    def _event(
        self, mission_id: str, event_type: EventType, message: str,
        metadata: dict[str, Any] | None = None, *, target_id: str | None = None,
        simulated: bool = False, severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        if mission_id != "unknown":
            self._active_mission_id = mission_id
        event = MissionEvent(
            mission_id=mission_id, vehicle_id=self.vehicle_id, target_id=target_id,
            type=event_type, severity=severity, message=message[:500], metadata=metadata or {}, simulated=simulated,
        )
        if self.artifacts:
            self.artifacts.append_event(event)
        self.event_callback(event)

    @staticmethod
    def _length(points: list[tuple[float, float]]) -> float:
        return sum(math.dist(a, b) for a, b in zip(points, points[1:]))

    @staticmethod
    def _append_distinct(points: list[tuple[float, float]], point: tuple[float, float], tolerance_m: float = .02) -> None:
        if not points or math.dist(points[-1], point) >= tolerance_m:
            points.append(point)

    def _finish_artifacts(
        self, execution: MissionExecution, plan: CoveragePlan, scenario: SyntheticSurvivorScenario | None,
    ) -> None:
        def coords(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
            return points
        self.artifacts.write_geojson("actual_search_trail_enu.geojson", coords(self.search_trail), {"phase": "AUTO_SEARCH"})
        self.artifacts.write_geojson("guided_diversion_trail_enu.geojson", coords(self.guided_trail), {"phase": "GUIDED_DIVERSION"})
        summary = execution.model_dump(mode="json")
        summary["synthetic_target_ground_truth"] = scenario.model_dump(mode="json") if scenario else None
        summary["report_markdown"] = self._report(execution, plan, scenario)
        self.artifacts.finish(summary)

    def _report(self, execution: MissionExecution, plan: CoveragePlan, scenario: SyntheticSurvivorScenario | None) -> str:
        payload = execution.metadata.get("payload") or {}
        checkpoint = execution.metadata.get("search_resume_checkpoint") or {}
        target = execution.metadata.get("target") or {}
        metrics = execution.metadata.get("metrics") or {}
        if scenario is not None:
            target_error = math.hypot(
                float(target.get("east_m", scenario.true_east_m)) - scenario.true_east_m,
                float(target.get("north_m", scenario.true_north_m)) - scenario.true_north_m,
            )
            scenario_pos = f"({scenario.true_east_m:.2f}, {scenario.true_north_m:.2f}) m"
            geo_error_str = f"{target_error:.6f} m"
        else:
            scenario_pos = "LIVE_PERCEPTION"
            geo_error_str = "N/A"
        duration = (
            (execution.completed_at - execution.started_at).total_seconds()
            if execution.started_at and execution.completed_at else 0
        )
        return f"""# Stage 7S Rescue Mission Report

## Hardware Gate

- H1: **PENDING** — physical hardware temporarily unavailable
- Physical hardware touched: **NO**

## Search

- Boundary planned coverage: {plan.validation.planned_coverage_percent:.2f}%
- Actual final search coverage: {execution.actual_coverage_percent:.2f}%
- Search distance: {metrics.get('actual_search_distance_m', 0):.2f} m
- Missed area: {execution.missed_area_m2 or 0:.2f} m²
- Repeated/overlap area: {execution.overlap_area_m2 or 0:.2f} m²

## Target (SITL / SYNTHETIC)

- Synthetic position ENU: {scenario_pos}
- Observations: {target.get('observation_count', 0)}
- First detection: {metrics.get('time_to_first_detection_s', 'NOT_TESTED')} s after mission start
- Detection to confirmation: {metrics.get('detection_to_confirmation_s', 'NOT_TESTED')} s
- Synthetic fused geolocation error: {geo_error_str}
- Fused uncertainty: {target.get('uncertainty_m', 'UNKNOWN')} m

## Interruption / Resume

- Mission sequence at interruption: {checkpoint.get('current_mission_seq', 'NOT_TESTED')}
- Last reached: {checkpoint.get('last_reached_mission_seq', 'NOT_TESTED')}
- Policy: {execution.metadata.get('resume_policy', 'NOT_TESTED')}
- Requested / actual: {self.resume_requested_seq} / {self.resume_actual_seq}

## Delivery (SIMULATED ONLY)

- Release: {payload.get('release_succeeded', False)}
- Verification: {payload.get('verification_succeeded', False)}
- Horizontal error: {payload.get('relative_target_error_m', 'NOT_TESTED')} m
- Altitude: {payload.get('altitude_m', 'NOT_TESTED')} m
- Ground speed: {payload.get('ground_speed_m_s', 'NOT_TESTED')} m/s

## Service timing

- Confirmation to GUIDED: {metrics.get('confirmation_to_guided_s', 'NOT_TESTED')} s
- Coarse approach: {metrics.get('target_approach_time_s', 'NOT_TESTED')} s
- Reacquisition: {metrics.get('reacquisition_time_s', 'NOT_TESTED')} s
- Alignment: {metrics.get('alignment_time_s', 'NOT_TESTED')} s
- Delivery: {metrics.get('delivery_time_s', 'NOT_TESTED')} s
- AUTO resume: {metrics.get('resume_time_s', 'NOT_TESTED')} s
- Total service overhead: {metrics.get('service_total_time_s', 'NOT_TESTED')} s

## Mission

- Mode history: {' -> '.join(self.mode_history)}
- Total actual distance: {execution.actual_path_length_m:.2f} m
- Total mission duration: {duration:.2f} s
- RTL: {execution.rtl_completed}
- Landing: {execution.landing_detected}
- Disarm: {execution.disarm_detected}
- Final state: {execution.state}
"""
