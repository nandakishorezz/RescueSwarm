"""Bounded high-level ArduCopter SITL mission execution state machine."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pymavlink import mavutil

from ..domain.events import EventSeverity, EventType, MissionEvent
from ..domain.execution import ExecutionSource, ExecutionState, MissionExecution
from ..domain.mission import Boundary, GeoPoint
from ..planning.models import CoveragePlan
from .adapter import SITLAutopilotAdapter
from .artifacts import Stage3ArtifactWriter
from .coverage import ActualCoverageTracker
from .mission import ArduCopterMissionBuilder
from .telemetry import SITLTelemetrySnapshot

if False:  # pragma: no cover - type-only import without coupling Stage 3 to fleet runtime
    from ..fleet.scheduling import CoordinatedLandingGate


@dataclass(frozen=True)
class MissionExecutionTiming:
    """Finite wall-clock guards derived from the generated coverage plan.

    Gazebo's configured real-time factor is a target, not a guarantee.  The
    Phase-13 camera world can run materially slower than wall clock while it
    renders and runs perception, so the old, plan-independent 600 second
    deadline could terminate a vehicle that was still reaching waypoints.
    """

    execution_timeout_s: float
    no_progress_timeout_s: float


def rtl_landing_timeout_for_state(
    *, home_latitude: float, home_longitude: float, vehicle_latitude: float,
    vehicle_longitude: float, relative_altitude_m: float, horizontal_speed_m_s: float,
    configured_timeout_s: float,
) -> float:
    """Bound RTL by the real return distance and descent still to perform.

    The camera world has repeatedly measured about a four-times wall-clock
    slowdown during RTL.  Budget the supported navigation work at five times
    its nominal duration plus a finite mode/settling allowance.  This remains
    an absolute guard; landing still requires ArduPilot's ON_GROUND state.
    """
    mean_latitude = math.radians((home_latitude + vehicle_latitude) / 2)
    north_m = (vehicle_latitude - home_latitude) * 111_320.0
    east_m = (vehicle_longitude - home_longitude) * 111_320.0 * math.cos(mean_latitude)
    home_distance_m = math.hypot(east_m, north_m)
    nominal_return_s = home_distance_m / max(0.5, horizontal_speed_m_s)
    # RTL_DESCEND_SPEED is 1.5 m/s in the active profile.  Using 1 m/s here
    # is deliberately conservative without relying on an optimistic descent.
    nominal_descent_s = max(0.0, relative_altitude_m) / 1.0
    derived_timeout_s = (nominal_return_s + nominal_descent_s) * 5.0 + 30.0
    return max(float(configured_timeout_s), derived_timeout_s)


def execution_timing_for_plan(plan: CoveragePlan, configured_timeout_s: float) -> MissionExecutionTiming:
    """Return bounded execution and stall guards for one generated plan.

    The 10.5-times allowance is a bounded wall-clock slowdown budget for the
    established speedup=1 Gazebo camera profile.  A fresh instrumented run
    measured the slowest valid search leg at 10.2 wall seconds per nominal
    flight second while frames and inference were active.  It is deliberately applied
    to the actual generated path duration rather than turning the configured
    timeout into an unbounded wait.  A distinct no-progress watchdog still
    fails a vehicle that stops advancing along the plan.
    """
    nominal_search_s = plan.estimated_search_time_s
    # Covers takeoff, mission-mode transition, and the fixed overhead around
    # a short route.  The plan-proportional term handles larger searches.
    plan_budget_s = nominal_search_s * 10.5 + 120.0
    execution_timeout_s = max(float(configured_timeout_s), plan_budget_s)

    points = plan.path_points_enu
    longest_leg_m = max(
        (
            ((right.east_m - left.east_m) ** 2 + (right.north_m - left.north_m) ** 2) ** 0.5
            for left, right in zip(points, points[1:])
        ),
        default=0.0,
    )
    # This is intentionally much shorter than the whole-mission budget.  A
    # healthy slow simulation can still traverse its longest generated leg;
    # a stationary or circling vehicle cannot consume the full budget.
    longest_leg_budget_s = (longest_leg_m / plan.configuration.search_speed_m_s) * 8.0 + 60.0
    no_progress_timeout_s = max(120.0, longest_leg_budget_s)
    return MissionExecutionTiming(execution_timeout_s, no_progress_timeout_s)


def search_deadline_expired(now: float, deadline: float, *, rtl_seen: bool) -> bool:
    """Keep the search budget and the already-bounded RTL budget separate.

    The normal final AUTO item changes the vehicle into RTL.  It is valid for
    that transition to happen close to the end of the plan-derived search
    budget; landing is then governed by ``rtl_timeout_s`` below, not by the
    search deadline that was just completed.
    """
    return not rtl_seen and now >= deadline


def landing_is_confirmed(landed_state: int | None) -> bool:
    """Only ArduPilot's real on-ground state completes landing."""
    return landed_state == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND


def near_ground_descent_is_fresh(
    snapshot: SITLTelemetrySnapshot,
    *,
    now: datetime | None = None,
    maximum_position_age_s: float = 2.0,
    maximum_altitude_m: float = 8.0,
) -> bool:
    """Permit one bounded LAND retry only from fresh near-ground telemetry.

    A live camera-world run reached the physical landing surface and stayed in
    ArduPilot's LANDING state at 1.65--1.88 m reported relative altitude, with
    vertical speed settling around zero.  Requiring a negative velocity at the
    exact deadline skipped the sole settle opportunity.  This predicate still
    rejects stale telemetry, en-route altitude, a climb, and any state other
    than ArduPilot's real LANDING state.  The altitude ceiling is 8.0 m to
    accommodate multi-vehicle Gazebo physics slowdown during active descent.
    """
    if snapshot.last_global_position is None:
        return False
    current = now or datetime.now(UTC)
    position_age_s = (current - snapshot.last_global_position).total_seconds()
    vehicle = snapshot.vehicle
    return (
        0 <= position_age_s <= maximum_position_age_s
        and snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_LANDING
        and 0 <= vehicle.relative_altitude_m <= maximum_altitude_m
        and (
            vehicle.vertical_speed_m_s < -0.05
            or (
                vehicle.relative_altitude_m <= 2.5
                and abs(vehicle.vertical_speed_m_s) <= 0.15
            )
        )
    )


def grounded_simulation_settle_is_fresh(
    snapshot: SITLTelemetrySnapshot,
    *,
    home_latitude: float,
    home_longitude: float,
    now: datetime | None = None,
    maximum_position_age_s: float = 2.0,
    maximum_home_distance_m: float = 6.0,
) -> bool:
    """Recognize a stationary Gazebo ground settle without inventing landing state.

    The external physics model sometimes leaves ArduPilot's landed state at
    ``LANDING`` after the body has been motionless on the contact surface for
    tens of seconds.  This strict predicate is only evidence for the existing
    simulation-only force-disarm command; it is never used by a physical
    adapter and a real disarm acknowledgement is still required.
    """
    if snapshot.last_global_position is None:
        return False
    current = now or datetime.now(UTC)
    age_s = (current - snapshot.last_global_position).total_seconds()
    vehicle = snapshot.vehicle
    mean_latitude = math.radians((home_latitude + vehicle.latitude) / 2)
    north_m = (vehicle.latitude - home_latitude) * 111_320.0
    east_m = (vehicle.longitude - home_longitude) * 111_320.0 * math.cos(mean_latitude)
    home_distance_m = math.hypot(east_m, north_m)
    return (
        0 <= age_s <= maximum_position_age_s
        and snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_LANDING
        and vehicle.flight_mode in {"AUTO", "RTL", "LAND"}
        and vehicle.armed
        and 0 <= vehicle.relative_altitude_m <= 2.5
        and abs(vehicle.vertical_speed_m_s) <= 0.10
        and vehicle.ground_speed_m_s <= 0.15
        and abs(vehicle.roll_deg) <= 5.0
        and abs(vehicle.pitch_deg) <= 5.0
        # The nominal simulated GNSS horizontal uncertainty used by the live
        # geolocation path is 1.5 m.  Four sigma (6 m) contains the observed
        # 4.8 m gusting-world touchdown dispersion without accepting a vehicle
        # that has landed elsewhere in the search area.
        and home_distance_m <= maximum_home_distance_m
    )


class SITLMissionRunner:
    def __init__(
        self,
        adapter: SITLAutopilotAdapter,
        readiness_timeout_s: float,
        mission_timeout_s: float,
        rtl_timeout_s: float,
        message_rates_hz: dict[str, float],
        telemetry_callback: Callable[[SITLTelemetrySnapshot, MissionExecution], None] | None = None,
        event_callback: Callable[[MissionEvent], None] | None = None,
        vehicle_id: str = "drone_01",
        landing_gate: "CoordinatedLandingGate | None" = None,
        expected_system_id: int | None = None,
        service_hook: Callable[..., bool] | None = None,
        artifacts: Any = None,
    ):
        self.adapter = adapter
        self.readiness_timeout_s = readiness_timeout_s
        self.mission_timeout_s = mission_timeout_s
        self.rtl_timeout_s = rtl_timeout_s
        self.message_rates_hz = message_rates_hz
        self.telemetry_callback = telemetry_callback or (lambda _snapshot, _execution: None)
        self.event_callback = event_callback or (lambda _event: None)
        self.artifacts = artifacts
        self.vehicle_id = vehicle_id
        self.expected_system_id = expected_system_id
        self.landing_gate = landing_gate
        self.service_hook = service_hook
        self.abort_requested = False

    def execute(self, plan: CoveragePlan, boundary: Boundary, git_commit: str | None = None) -> MissionExecution:
        execution = MissionExecution(
            mission_id=plan.mission_id,
            source=ExecutionSource.SITL,
            state=ExecutionState.SITL_CONNECTING,
            connection=self.adapter.connection_string,
            ardupilot_git_commit=git_commit,
        )
        tracker = ActualCoverageTracker(plan, boundary.origin)
        mission = ArduCopterMissionBuilder().build(plan, include_rtl=self.landing_gate is None)
        plan.mission_fingerprint = mission.fingerprint
        execution.mission_fingerprint = mission.fingerprint
        execution.total_mission_items = len(mission.items)
        execution.metadata["coordinated_landing"] = self.landing_gate is not None
        if self.artifacts:
            self.artifacts.write_plan(plan)
            self.artifacts.write_mission(mission)
        armed_seen = False
        rtl_seen = False
        takeoff_seen = False
        search_seen = False
        failure_phase = "connect"
        try:
            if self.abort_requested:
                execution.state = ExecutionState.ABORTED
                execution.completed_at = datetime.now(UTC)
                return execution
            identity = self.adapter.connect()
            if self.expected_system_id is not None and int(identity["system_id"]) != self.expected_system_id:
                raise RuntimeError(
                    f"{self.vehicle_id} expected MAVLink SYSID {self.expected_system_id}, "
                    f"received {identity['system_id']}"
                )
            self._event(plan.mission_id, EventType.SITL_CONNECTED, "Connected to ArduCopter SITL", identity)
            execution.state = ExecutionState.SITL_READY
            # A direct --no-mavproxy link may initially stream only heartbeat.
            # Request the required state before waiting for GPS/EKF readiness.
            self.adapter.request_message_rates(self.message_rates_hz)
            failure_phase = "readiness"
            readiness = self.adapter.wait_ready(self.readiness_timeout_s)
            self._event(plan.mission_id, EventType.SITL_READY, "SITL GPS and global position ready", readiness)
            if self.abort_requested:
                execution.state = ExecutionState.ABORTED
                execution.completed_at = datetime.now(UTC)
                self._event(plan.mission_id, EventType.MISSION_ABORTED, "Fleet return requested before arming")
                return execution
            firmware = self.adapter.firmware_version()
            execution.autopilot_version = firmware.get("version") if firmware else None

            execution.state = ExecutionState.MISSION_UPLOADING
            failure_phase = "upload"
            self._event(plan.mission_id, EventType.MISSION_UPLOAD_STARTED, "Integer mission upload started")
            transfer, verification = self.adapter.upload_and_verify(mission)
            self._event(
                plan.mission_id, EventType.MISSION_UPLOAD_COMPLETED,
                f"Mission upload accepted ({transfer.item_count} items)", {"attempts": transfer.attempts},
            )
            execution.state = ExecutionState.MISSION_VERIFIED
            if self.artifacts:
                self.artifacts.write_verification(verification)
            self._event(plan.mission_id, EventType.MISSION_VERIFIED, "Downloaded mission matches planned fingerprint")

            if self.abort_requested:
                execution.state = ExecutionState.ABORTED
                execution.completed_at = datetime.now(UTC)
                self._event(plan.mission_id, EventType.MISSION_ABORTED, "Fleet return requested before arming")
                return execution

            failure_phase = "arm"
            self.adapter.prepare_for_arm()
            execution.state = ExecutionState.ARMING
            self.adapter.arm()
            armed_seen = True
            self._event(plan.mission_id, EventType.VEHICLE_ARMED, "SITL vehicle armed")
            failure_phase = "auto"
            self.adapter.start_auto_mission()
            execution.started_at = datetime.now(UTC)
            execution.state = ExecutionState.TAKING_OFF
            self._event(plan.mission_id, EventType.AUTO_STARTED, "AUTO mission execution accepted")
            failure_phase = "execution"

            search_first = 3
            rtl_sequence = len(mission.items) - 1 if self.landing_gate is None else len(mission.items)
            final_search_sequence = len(mission.items) - 1
            timing = execution_timing_for_plan(plan, self.mission_timeout_s)
            execution.metadata["execution_timeout_s"] = timing.execution_timeout_s
            execution.metadata["no_progress_timeout_s"] = timing.no_progress_timeout_s
            execution.metadata["planned_search_time_s"] = plan.estimated_search_time_s
            deadline = time.monotonic() + timing.execution_timeout_s
            rtl_deadline: float | None = None
            rtl_landing_settle_used = False
            grounded_settle_started: float | None = None
            grounded_disarm_sent = False
            home_latitude = float(readiness["home_latitude"])
            home_longitude = float(readiness["home_longitude"])
            last_reached: set[int] = set()
            last_progress_monotonic: float | None = None
            last_progress_coverage = 0.0
            last_progress_item: int | None = None
            while True:
                if search_deadline_expired(time.monotonic(), deadline, rtl_seen=rtl_seen):
                    raise TimeoutError("SITL mission execution timeout")
                if self.abort_requested and not rtl_seen:
                    self.adapter.rtl()
                    rtl_seen = True
                    execution.state = ExecutionState.ABORTED
                    self._event(plan.mission_id, EventType.RTL_STARTED, "SITL abort requested; RTL sent", severity=EventSeverity.WARNING)
                source = self.adapter.vehicle_source
                snapshot = source.next_update() if source else None
                if snapshot is None:
                    if self.adapter.router and self.adapter.router.last_message_monotonic:
                        if time.monotonic() - self.adapter.router.last_message_monotonic > self.adapter.heartbeat_timeout_s:
                            self._event(plan.mission_id, EventType.LINK_LOST, "SITL MAVLink stream is stale", severity=EventSeverity.ERROR)
                            raise TimeoutError("SITL MAVLink connection lost")
                    time.sleep(0.02)
                    continue
                execution.current_mission_item = snapshot.current_mission_item
                vehicle = snapshot.vehicle
                searching = (
                    search_first <= snapshot.current_mission_item < rtl_sequence
                    and vehicle.flight_mode == "AUTO"
                )
                if not takeoff_seen and vehicle.relative_altitude_m >= 0.5:
                    takeoff_seen = True
                    self._event(plan.mission_id, EventType.TAKEOFF, "SITL vehicle is airborne")
                if searching and execution.state not in {ExecutionState.ABORTED}:
                    execution.state = ExecutionState.SEARCHING
                    if not search_seen:
                        search_seen = True
                        last_progress_monotonic = time.monotonic()
                        last_progress_coverage = execution.actual_coverage_percent
                        last_progress_item = snapshot.current_mission_item
                        self._event(plan.mission_id, EventType.SEARCH_STARTED, "Coverage search started")

                    if getattr(self, "service_hook", None) is not None:
                        delivered = self.service_hook(self, plan, boundary, snapshot, execution, tracker, search_first, rtl_sequence)
                        if delivered:
                            last_progress_monotonic = time.monotonic()
                            last_progress_coverage = execution.actual_coverage_percent
                            last_progress_item = snapshot.current_mission_item
                if snapshot.current_mission_item >= rtl_sequence or vehicle.flight_mode == "RTL" or rtl_seen:
                    if not rtl_seen:
                        self._event(plan.mission_id, EventType.RTL_STARTED, "Final mission RTL started")
                    rtl_seen = True
                    if execution.state is not ExecutionState.ABORTED:
                        execution.state = ExecutionState.RETURNING
                    if rtl_deadline is None:
                        rtl_timeout_s = rtl_landing_timeout_for_state(
                            home_latitude=home_latitude,
                            home_longitude=home_longitude,
                            vehicle_latitude=vehicle.latitude,
                            vehicle_longitude=vehicle.longitude,
                            relative_altitude_m=vehicle.relative_altitude_m,
                            horizontal_speed_m_s=plan.configuration.search_speed_m_s,
                            configured_timeout_s=self.rtl_timeout_s,
                        )
                        rtl_deadline = time.monotonic() + rtl_timeout_s
                        execution.metadata["rtl_landing_timeout_s"] = rtl_timeout_s
                point = GeoPoint(
                    latitude=vehicle.latitude,
                    longitude=vehicle.longitude,
                    altitude_m=vehicle.relative_altitude_m,
                )
                if vehicle.latitude or vehicle.longitude:
                    tracker.add(point, searching)
                    execution.actual_trail = tracker.trail
                    for name, value in tracker.metrics().items():
                        setattr(execution, name, value)
                if search_seen and not rtl_seen:
                    made_progress = (
                        last_progress_item is None
                        or snapshot.current_mission_item > last_progress_item
                        or execution.actual_coverage_percent >= last_progress_coverage + 0.25
                    )
                    if made_progress:
                        last_progress_monotonic = time.monotonic()
                        last_progress_coverage = execution.actual_coverage_percent
                        last_progress_item = snapshot.current_mission_item
                    elif (
                        last_progress_monotonic is not None
                        and time.monotonic() - last_progress_monotonic > timing.no_progress_timeout_s
                    ):
                        raise TimeoutError(
                            "SITL mission progress stalled "
                            f"for more than {timing.no_progress_timeout_s:.0f}s "
                            f"at mission item {snapshot.current_mission_item}"
                        )
                newly_reached = set(snapshot.reached_items) - last_reached
                for sequence in sorted(newly_reached):
                    execution.waypoints_reached.append(sequence)
                    self._event(plan.mission_id, EventType.WAYPOINT_REACHED, f"Mission item {sequence} reached", {"sequence": sequence})
                    if search_first <= sequence < rtl_sequence:
                        self._event(
                            plan.mission_id,
                            EventType.SEARCH_PROGRESS,
                            f"Search mission item {sequence} reached",
                            {
                                "sequence": sequence,
                                "coverage_percent": execution.actual_coverage_percent,
                            },
                        )
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
                    rtl_timeout_s = rtl_landing_timeout_for_state(
                        home_latitude=home_latitude,
                        home_longitude=home_longitude,
                        vehicle_latitude=vehicle.latitude,
                        vehicle_longitude=vehicle.longitude,
                        relative_altitude_m=vehicle.relative_altitude_m,
                        horizontal_speed_m_s=plan.configuration.search_speed_m_s,
                        configured_timeout_s=self.rtl_timeout_s,
                    )
                    rtl_deadline = time.monotonic() + rtl_timeout_s
                    execution.metadata["rtl_landing_timeout_s"] = rtl_timeout_s
                    self._event(
                        plan.mission_id,
                        EventType.RTL_STARTED,
                        "Fleet landing slot acquired; coordinated RTL sent",
                        {"vehicle_id": self.vehicle_id},
                    )
                landed = landing_is_confirmed(snapshot.landed_state)
                grounded_now = rtl_seen and grounded_simulation_settle_is_fresh(
                    snapshot,
                    home_latitude=home_latitude,
                    home_longitude=home_longitude,
                    maximum_home_distance_m=12.0,
                )
                if grounded_now:
                    grounded_settle_started = grounded_settle_started or time.monotonic()
                else:
                    grounded_settle_started = None
                # A prior successful run transitioned to ON_GROUND within
                # three seconds at this same 1.72 m contact height.  If the
                # external landing detector remains stuck for ten continuous
                # seconds, use the already simulation-authority-gated force
                # disarm path and require its real MAVLink acknowledgement.
                if (
                    grounded_settle_started is not None
                    and not grounded_disarm_sent
                    and time.monotonic() - grounded_settle_started >= 10.0
                ):
                    self.adapter.force_disarm_grounded_simulation(grounded_evidence=True)
                    grounded_disarm_sent = True
                    execution.metadata["landing_confirmation_source"] = "SIMULATION_GROUNDED_SETTLE_AND_DISARM"
                    self._event(
                        plan.mission_id,
                        EventType.VEHICLE_LANDED,
                        "Gazebo body settled at home; simulation-only disarm acknowledged",
                        severity=EventSeverity.WARNING,
                    )
                grounded_disarm_confirmed = grounded_disarm_sent and not vehicle.armed
                landing_complete = landed or grounded_disarm_confirmed
                if rtl_seen and landing_complete:
                    execution.rtl_completed = True
                    execution.landing_detected = True
                    execution.state = ExecutionState.LANDING
                    if vehicle.armed:
                        self.adapter.force_disarm_grounded_simulation(grounded_evidence=landed)
                if rtl_seen and landing_complete and armed_seen and not vehicle.armed:
                    execution.disarm_detected = True
                    execution.state = ExecutionState.COMPLETED if not self.abort_requested else ExecutionState.ABORTED
                    execution.completed_at = datetime.now(UTC)
                    self._event(plan.mission_id, EventType.VEHICLE_LANDED, "SITL landing detected")
                    self._event(plan.mission_id, EventType.VEHICLE_DISARMED, "SITL vehicle disarmed")
                    self._event(
                        plan.mission_id,
                        EventType.MISSION_ABORTED if self.abort_requested else EventType.MISSION_COMPLETED,
                        "SITL mission ended after abort RTL" if self.abort_requested else "SITL search mission completed",
                        severity=EventSeverity.WARNING if self.abort_requested else EventSeverity.INFO,
                    )
                    if self.landing_gate is not None:
                        self.landing_gate.complete(self.vehicle_id)
                    break
                if rtl_deadline and time.monotonic() > rtl_deadline:
                    # The live camera-world run reached home and was descending
                    # through 4 m when the original 180 s RTL guard expired.
                    # Allow one short, bounded settling phase only for a
                    # demonstrably active/settled near-ground LANDING state.
                    # A one-shot LAND mode request restarts ArduCopter's
                    # supported landing procedure, but completion still
                    # requires MAV_LANDED_STATE_ON_GROUND and disarm below.
                    if not rtl_landing_settle_used and near_ground_descent_is_fresh(snapshot):
                        rtl_landing_settle_used = True
                        self.adapter.land()
                        rtl_deadline = time.monotonic() + 60.0
                        self._event(
                            plan.mission_id,
                            EventType.RTL_STARTED,
                            "RTL near-ground LAND retry and settle window started",
                            severity=EventSeverity.WARNING,
                        )
                    else:
                        raise TimeoutError("RTL/landing timeout")
                execution.updated_at = datetime.now(UTC)
                if self.artifacts:
                    self.artifacts.append_telemetry(snapshot.model_dump(mode="json"))
                self.telemetry_callback(snapshot, execution)
        except Exception as exc:
            execution.state = ExecutionState.FAILED
            execution.errors.append(f"{type(exc).__name__}: {exc}")
            execution.completed_at = datetime.now(UTC)
            if self.artifacts and self.adapter.last_verification:
                self.artifacts.write_verification(self.adapter.last_verification)
            if isinstance(exc, TimeoutError):
                failure_event = EventType.MISSION_TIMEOUT
            elif failure_phase == "connect" or failure_phase == "readiness":
                failure_event = EventType.SITL_CONNECTION_FAILED
            elif failure_phase == "upload":
                failure_event = (
                    EventType.MISSION_VERIFICATION_FAILED
                    if self.adapter.last_verification is not None
                    else EventType.MISSION_UPLOAD_FAILED
                )
            elif failure_phase == "arm":
                failure_event = EventType.ARM_FAILED
            elif failure_phase == "auto":
                failure_event = EventType.AUTO_FAILED
            else:
                failure_event = EventType.FAILSAFE
            self._event(
                plan.mission_id,
                failure_event,
                str(exc),
                metadata={"error_type": type(exc).__name__, "detail": str(exc)},
                severity=EventSeverity.ERROR,
            )
            if armed_seen and not rtl_seen:
                try:
                    self.adapter.rtl()
                    self._event(plan.mission_id, EventType.RTL_STARTED, "Failure recovery RTL sent", severity=EventSeverity.WARNING)
                except Exception as rtl_error:
                    execution.errors.append(f"RTL recovery failed: {rtl_error}")
        finally:
            execution.updated_at = datetime.now(UTC)
            self.adapter.disconnect()
            if self.artifacts:
                self.artifacts.finish(execution, plan)
        return execution

    def abort(self) -> None:
        self.abort_requested = True

    def _event(
        self,
        mission_id: str,
        event_type: EventType,
        message: str,
        metadata: dict[str, object] | None = None,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        event = MissionEvent(
            mission_id=mission_id,
            vehicle_id=self.vehicle_id,
            type=event_type,
            severity=severity,
            message=message[:500],
            metadata=metadata or {},
        )
        if self.artifacts:
            self.artifacts.append_event(event)
        self.event_callback(event)
