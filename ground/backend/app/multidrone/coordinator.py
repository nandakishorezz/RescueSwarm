"""Authoritative exclusive service coordinator for multi-drone operations.

Enforces:
- SEARCHER drones can only search and observe; they must NEVER receive service jobs.
- Only SERVICER drones can receive service assignments.
- Exclusivity: At most 1 active assignment per survivor, at most 1 active job per SERVICER.
- Servicer eligibility: Fresh telemetry (< 3s), connected, eligible operational state (READY/SEARCHING),
  not returning/landing/disarmed/failed.
- Deterministic selection policy when multiple SERVICERs are eligible.
- Explicit lifecycle transitions:
  QUEUED -> ASSIGNED -> ACKNOWLEDGED -> APPROACHING -> HOLDING_FOR_RELEASE ->
  RELEASED -> VERIFYING -> DELIVERED -> RESUMING_SEARCH -> COMPLETED (plus FAILED, CANCELLED).
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from .contracts import (
    AssignmentState,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    PrioritySource,
    ServiceAssignment,
    ServicePriority,
    SharedSurvivorRecord,
)
from .registry import SharedSurvivorRegistry


VALID_ASSIGNMENT_TRANSITIONS: dict[AssignmentState, set[AssignmentState]] = {
    AssignmentState.QUEUED: {AssignmentState.ASSIGNED, AssignmentState.CANCELLED, AssignmentState.FAILED},
    AssignmentState.ASSIGNED: {AssignmentState.ACKNOWLEDGED, AssignmentState.APPROACHING, AssignmentState.CANCELLED, AssignmentState.FAILED},
    AssignmentState.ACKNOWLEDGED: {AssignmentState.APPROACHING, AssignmentState.CANCELLED, AssignmentState.FAILED},
    AssignmentState.APPROACHING: {AssignmentState.HOLDING_FOR_RELEASE, AssignmentState.FAILED, AssignmentState.CANCELLED},
    AssignmentState.HOLDING_FOR_RELEASE: {AssignmentState.RELEASED, AssignmentState.FAILED, AssignmentState.CANCELLED},
    AssignmentState.RELEASED: {AssignmentState.VERIFYING, AssignmentState.FAILED},
    AssignmentState.VERIFYING: {AssignmentState.DELIVERED, AssignmentState.FAILED},
    AssignmentState.DELIVERED: {AssignmentState.RESUMING_SEARCH, AssignmentState.COMPLETED, AssignmentState.FAILED},
    AssignmentState.RESUMING_SEARCH: {AssignmentState.COMPLETED, AssignmentState.FAILED},
    AssignmentState.COMPLETED: set(),
    AssignmentState.CANCELLED: set(),
    AssignmentState.FAILED: {AssignmentState.QUEUED},
}

ACTIVE_SERVICE_STATES: set[AssignmentState] = {
    AssignmentState.ASSIGNED,
    AssignmentState.ACKNOWLEDGED,
    AssignmentState.APPROACHING,
    AssignmentState.HOLDING_FOR_RELEASE,
    AssignmentState.RELEASED,
    AssignmentState.VERIFYING,
    AssignmentState.DELIVERED,
    AssignmentState.RESUMING_SEARCH,
}

PRIORITY_ORDER = {
    ServicePriority.CRITICAL: 0,
    ServicePriority.HIGH: 1,
    ServicePriority.NORMAL: 2,
    ServicePriority.LOW: 3,
}
MAX_QUEUED_TARGET_AGE_S = 300.0


class ExclusiveServiceCoordinator:
    """Thread-safe authoritative coordinator for service job allocation and execution."""

    def __init__(
        self,
        session: FleetSession,
        fleet_config: FleetConfiguration,
        registry: SharedSurvivorRegistry,
        *,
        vehicles_state_supplier: Callable[[], dict[str, dict]] | None = None,
        event_callback: Callable[[str | None, str, str, str, dict | None], None] | None = None,
    ):
        self.session = session
        self.fleet_config = fleet_config
        self.registry = registry
        self.vehicles_state_supplier = vehicles_state_supplier
        self.event_callback = event_callback

        self._lock = threading.RLock()
        self._assignments: dict[UUID, ServiceAssignment] = {}
        self._survivor_to_assignment: dict[str, UUID] = {}
        self._servicer_to_assignment: dict[str, UUID] = {}

    def reset(self) -> None:
        with self._lock:
            self._assignments.clear()
            self._survivor_to_assignment.clear()
            self._servicer_to_assignment.clear()

    def assignments(self) -> list[ServiceAssignment]:
        with self._lock:
            ordered = sorted(self._assignments.values(), key=self._job_order)
            queue_position = 0
            result = []
            for assignment in ordered:
                copy = assignment.model_copy(deep=True)
                if copy.state == AssignmentState.QUEUED:
                    queue_position += 1
                    copy.queue_position = queue_position
                result.append(copy)
            return result

    def _job_order(self, assignment: ServiceAssignment) -> tuple:
        survivor = self.registry.get_survivor(assignment.canonical_survivor_id)
        confirmed_at = survivor.confirmed_at if survivor else assignment.created_at
        return (PRIORITY_ORDER[ServicePriority(assignment.priority)], confirmed_at,
                assignment.canonical_survivor_id)

    def set_priority(
        self, canonical_survivor_id: str, priority: ServicePriority,
        source: PrioritySource,
    ) -> SharedSurvivorRecord:
        with self._lock:
            record = self.registry.update_priority(canonical_survivor_id, priority, source)
            assignment_id = self._survivor_to_assignment.get(canonical_survivor_id)
            if assignment_id is not None:
                assignment = self._assignments[assignment_id]
                assignment.priority = record.priority
                assignment.priority_source = record.priority_source
                assignment.updated_at = datetime.now(UTC)
            if self.event_callback:
                self.event_callback(None, "SURVIVOR_PRIORITY_CHANGED",
                    f"{canonical_survivor_id} priority set to {record.priority} by {record.priority_source}",
                    "INFO", {"canonical_id": canonical_survivor_id,
                             "priority": record.priority.value, "source": record.priority_source.value})
            self.reconcile_queue()
            return record

    def get_assignment(self, assignment_id: UUID) -> ServiceAssignment | None:
        with self._lock:
            a = self._assignments.get(assignment_id)
            return a.model_copy(deep=True) if a else None

    def get_active_assignment_for_servicer(self, servicer_id: str) -> ServiceAssignment | None:
        """Return the active service assignment currently assigned to the given servicer."""
        with self._lock:
            aid = self._servicer_to_assignment.get(servicer_id)
            if aid is not None:
                assignment = self._assignments.get(aid)
                if assignment and assignment.state in ACTIVE_SERVICE_STATES:
                    return assignment.model_copy(deep=True)
            return None

    def on_survivor_confirmed(self, survivor: SharedSurvivorRecord) -> ServiceAssignment:
        """Triggered when a survivor is confirmed in the shared registry."""
        return self.allocate_job(survivor.canonical_id)

    def allocate_job(
        self,
        canonical_survivor_id: str,
        *,
        force_servicer_id: str | None = None,
    ) -> ServiceAssignment:
        """Allocate or queue a service assignment for a confirmed survivor."""
        with self._lock:
            # 1. Reject if survivor is not in confirmed registry
            survivor = self.registry.get_survivor(canonical_survivor_id)
            if survivor is None or survivor.confirmation_status != "CONFIRMED":
                raise ValueError(f"Survivor {canonical_survivor_id} is not confirmed in the shared registry")

            # 2. Explicit role enforcement if a specific vehicle is requested
            if force_servicer_id is not None:
                drone_inst = next(
                    (d for d in self.fleet_config.drones if d.vehicle_id == force_servicer_id),
                    None,
                )
                if drone_inst is None:
                    raise ValueError(f"Vehicle {force_servicer_id} not in fleet session")
                if drone_inst.role is DroneRole.SEARCHER:
                    raise PermissionError(
                        f"SEARCHER vehicle {force_servicer_id} cannot receive service assignments"
                    )

            # 3. Idempotency: If survivor already has an active, queued, or released/completed job, return it
            existing_id = self._survivor_to_assignment.get(canonical_survivor_id)
            if existing_id is not None:
                existing = self._assignments.get(existing_id)
                if existing and (
                    existing.state == AssignmentState.QUEUED
                    or existing.state in ACTIVE_SERVICE_STATES
                    or existing.state == AssignmentState.COMPLETED
                    or existing.payload_released
                ):
                    return existing.model_copy(deep=True)

            # Older queued work gets first opportunity when capacity returns.
            self.reconcile_queue()

            # 4. Check eligibility of SERVICERs
            assignment_id = uuid4()
            eligible_servicers, reasons = self._find_eligible_servicers(survivor)

            if force_servicer_id is not None:
                # Direct assignment override (if eligible)
                if force_servicer_id in eligible_servicers:
                    selected_servicer = force_servicer_id
                else:
                    selected_servicer = None
                    reasons.insert(0, f"Requested vehicle {force_servicer_id} is not currently eligible")
            else:
                selected_servicer = self._select_best_servicer(eligible_servicers, survivor)

            now = datetime.now(UTC)
            if selected_servicer is not None:
                # Active exclusive assignment
                assignment = ServiceAssignment(
                    assignment_id=assignment_id,
                    session_id=self.session.session_id,
                    canonical_survivor_id=canonical_survivor_id,
                    servicer_id=selected_servicer,
                    state=AssignmentState.ASSIGNED,
                    reason=f"Exclusively assigned to eligible servicer {selected_servicer}",
                    priority=survivor.priority,
                    priority_source=survivor.priority_source,
                    created_at=now,
                    updated_at=now,
                    state_history=[{
                        "from_state": "NONE",
                        "to_state": AssignmentState.ASSIGNED.value,
                        "timestamp": now.isoformat(),
                        "reason": f"Exclusively assigned to eligible servicer {selected_servicer}",
                    }],
                )
                self._assignments[assignment_id] = assignment
                self._survivor_to_assignment[canonical_survivor_id] = assignment_id
                self._servicer_to_assignment[selected_servicer] = assignment_id

                self.registry.update_assignment(
                    canonical_survivor_id,
                    str(assignment_id),
                    selected_servicer,
                    AssignmentState.ASSIGNED,
                )

                if self.event_callback:
                    self.event_callback(
                        selected_servicer,
                        "JOB_ASSIGNED",
                        f"Service job {str(assignment_id)[:8]} assigned to {selected_servicer} for survivor {canonical_survivor_id}",
                        "INFO",
                        {
                            "assignment_id": str(assignment_id),
                            "canonical_id": canonical_survivor_id,
                            "servicer_id": selected_servicer,
                        },
                    )
            else:
                # Queued assignment with reason
                queue_reason = "; ".join(reasons) if reasons else "No eligible SERVICER drone available in fleet"
                assignment = ServiceAssignment(
                    assignment_id=assignment_id,
                    session_id=self.session.session_id,
                    canonical_survivor_id=canonical_survivor_id,
                    servicer_id=None,
                    state=AssignmentState.QUEUED,
                    reason=queue_reason,
                    priority=survivor.priority,
                    priority_source=survivor.priority_source,
                    created_at=now,
                    updated_at=now,
                    state_history=[{
                        "from_state": "NONE",
                        "to_state": AssignmentState.QUEUED.value,
                        "timestamp": now.isoformat(),
                        "reason": queue_reason,
                    }],
                )
                self._assignments[assignment_id] = assignment
                self._survivor_to_assignment[canonical_survivor_id] = assignment_id

                self.registry.update_assignment(
                    canonical_survivor_id,
                    str(assignment_id),
                    None,
                    AssignmentState.QUEUED,
                )

                if self.event_callback:
                    self.event_callback(
                        None,
                        "JOB_QUEUED",
                        f"Service job queued for survivor {canonical_survivor_id}: {queue_reason}",
                        "WARNING",
                        {
                            "assignment_id": str(assignment_id),
                            "canonical_id": canonical_survivor_id,
                            "reason": queue_reason,
                        },
                    )

            return assignment.model_copy(deep=True)

    def transition_assignment(
        self,
        assignment_id: UUID,
        target_state: AssignmentState,
        reason: str,
        metadata: dict | None = None,
    ) -> ServiceAssignment:
        """Authoritative assignment state transition engine with validated progression."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            cur_state = AssignmentState(assignment.state)
            target_state = AssignmentState(target_state)

            if cur_state == target_state:
                return assignment.model_copy(deep=True)

            valid_targets = VALID_ASSIGNMENT_TRANSITIONS.get(cur_state, set())
            if target_state not in valid_targets:
                raise ValueError(
                    f"Invalid assignment state transition from {cur_state} to {target_state}. "
                    f"Valid targets: {sorted(t.value for t in valid_targets)}"
                )

            now = datetime.now(UTC)
            assignment.state_history.append({
                "from_state": cur_state.value,
                "to_state": target_state.value,
                "timestamp": now.isoformat(),
                "reason": reason,
                "metadata": metadata or {},
            })
            assignment.state = target_state
            assignment.reason = reason
            assignment.updated_at = now

            self.registry.update_assignment(
                assignment.canonical_survivor_id,
                str(assignment_id),
                assignment.servicer_id,
                target_state,
            )

            if self.event_callback:
                self.event_callback(
                    assignment.servicer_id,
                    f"ASSIGNMENT_{target_state.value}",
                    f"Assignment {str(assignment_id)[:8]} transitioned to {target_state.value}: {reason}",
                    "INFO" if target_state not in {AssignmentState.FAILED, AssignmentState.CANCELLED} else "WARNING",
                    {"assignment_id": str(assignment_id), "state": target_state.value, "reason": reason},
                )

            return assignment.model_copy(deep=True)

    def acknowledge_assignment(self, assignment_id: UUID) -> ServiceAssignment:
        """Acknowledge receipt of an assigned service job."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state == AssignmentState.ACKNOWLEDGED:
                return assignment.model_copy(deep=True)

            if assignment.state != AssignmentState.ASSIGNED:
                raise RuntimeError(
                    f"Cannot acknowledge assignment in state {assignment.state}; must be ASSIGNED"
                )

            return self.transition_assignment(
                assignment_id,
                AssignmentState.ACKNOWLEDGED,
                f"Assignment acknowledged by {assignment.servicer_id}",
            )

    def check_approach_safety(
        self,
        assignment_id: UUID,
        *,
        telemetry_override: dict | None = None,
        boundary: Any = None,
    ) -> tuple[bool, str | None]:
        """Safety gate evaluation for vehicle approach to assigned survivor."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                return False, f"Assignment {assignment_id} not found"

            vid = assignment.servicer_id
            if not vid:
                return False, "Assignment has no servicer assigned"

            # 1. Role verification: SEARCHER is strictly prohibited
            drone_inst = next((d for d in self.fleet_config.drones if d.vehicle_id == vid), None)
            if drone_inst is None:
                return False, f"Vehicle {vid} not in fleet configuration"
            if drone_inst.role is DroneRole.SEARCHER:
                return False, f"SEARCHER vehicle {vid} is strictly forbidden from executing service jobs"

            # 2. Canonical survivor verification
            survivor = self.registry.get_survivor(assignment.canonical_survivor_id)
            if survivor is None:
                return False, f"Survivor {assignment.canonical_survivor_id} not found in registry"
            if survivor.confirmation_status != "CONFIRMED":
                return False, f"Survivor {assignment.canonical_survivor_id} is not confirmed in shared registry"

            has_gps = (survivor.latitude is not None and survivor.longitude is not None)
            has_enu = (survivor.east_m is not None and survivor.north_m is not None)
            if not (has_gps or has_enu):
                return False, f"Survivor {assignment.canonical_survivor_id} has invalid or missing geolocation"

            # Uncertainty check: must not exceed 8.0m (fail closed if missing or excessive)
            if survivor.uncertainty_m is None or survivor.uncertainty_m > 8.0:
                return False, f"Survivor uncertainty ({survivor.uncertainty_m}m) exceeds acceptable limit (8.0m)"

            # 3. Telemetry freshness & connection check
            vehicles_dict = self.vehicles_state_supplier() if self.vehicles_state_supplier else {}
            vinfo = telemetry_override or vehicles_dict.get(vid)
            if vinfo is None:
                return False, f"Vehicle {vid} has no telemetry in current session"

            connected = vinfo.get("connected", False)
            telem_age = vinfo.get("telemetry_age_s")
            if not connected or telem_age is None or telem_age > 3.0:
                age_str = f"{telem_age:.1f}s" if telem_age is not None else "unknown"
                return False, f"Vehicle {vid} telemetry is stale or disconnected (age: {age_str})"

            # 4. Operational state check
            v_state = vinfo.get("state")
            ineligible_states = {"RETURNING", "LANDING", "LANDED", "DISARMED", "FAILED", "STOPPED"}
            if v_state in ineligible_states:
                return False, f"Vehicle {vid} is in ineligible operational state '{v_state}'"

            # Telemetry detail: armed and flight mode
            telem = vinfo.get("telemetry") or {}
            mode = telem.get("flight_mode") or telem.get("mode")
            armed = telem.get("armed", False)
            if not armed:
                return False, f"Vehicle {vid} is not armed"
            if mode in {"RTL", "LAND"}:
                return False, f"Vehicle {vid} is in return/landing mode '{mode}'"

            # 5. Geofence / boundary check
            if boundary is not None and hasattr(boundary, "covers"):
                from shapely.geometry import Point
                v_east = telem.get("east_m")
                v_north = telem.get("north_m")
                if v_east is not None and v_north is not None:
                    if not boundary.covers(Point(v_east, v_north)):
                        return False, f"Vehicle {vid} is outside operational geofence"

            return True, None

    def start_approach(
        self,
        assignment_id: UUID,
        *,
        checkpoint_seq: int | None = None,
        telemetry_override: dict | None = None,
        boundary: Any = None,
    ) -> ServiceAssignment:
        """Initiate autonomous approach toward assigned survivor."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state not in {AssignmentState.ASSIGNED, AssignmentState.ACKNOWLEDGED}:
                raise RuntimeError(
                    f"Cannot start approach in state {assignment.state}; must be ASSIGNED or ACKNOWLEDGED"
                )

            safe, reason = self.check_approach_safety(
                assignment_id,
                telemetry_override=telemetry_override,
                boundary=boundary,
            )
            if not safe:
                raise RuntimeError(f"Approach safety gate blocked: {reason}")

            assignment.checkpoint_seq = checkpoint_seq
            return self.transition_assignment(
                assignment_id,
                AssignmentState.APPROACHING,
                f"Approach initiated toward survivor {assignment.canonical_survivor_id}",
                {"checkpoint_seq": checkpoint_seq},
            )

    def hold_for_release(
        self,
        assignment_id: UUID,
        *,
        approach_point: tuple[float, float, float] | None = None,
        delivery_point: tuple[float, float, float] | None = None,
        standoff_m: float | None = None,
    ) -> ServiceAssignment:
        """Signal arrival at standoff position; vehicle holds and prepares release safety gates."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state != AssignmentState.APPROACHING:
                raise RuntimeError(
                    f"Cannot transition to HOLDING_FOR_RELEASE in state {assignment.state}; must be APPROACHING"
                )

            if standoff_m is not None and standoff_m < 1.0:
                raise RuntimeError(
                    f"Unsafe standoff distance ({standoff_m:.2f}m); must be >= 1.0m to prevent payload dropping on person"
                )

            assignment.approach_point = approach_point
            assignment.delivery_point = delivery_point
            assignment.standoff_distance_m = standoff_m

            return self.transition_assignment(
                assignment_id,
                AssignmentState.HOLDING_FOR_RELEASE,
                "Holding at safe standoff delivery position; verifying release safety gates",
                {"standoff_m": standoff_m, "delivery_point": delivery_point},
            )

    def check_release_safety(
        self,
        assignment_id: UUID,
        *,
        telemetry_override: dict | None = None,
        delivery_altitude_m: float = 5.0,
        altitude_tolerance_m: float = 2.0,
        max_ground_speed_m_s: float = 0.8,
        max_positional_error_m: float = 2.5,
        boundary: Any = None,
    ) -> tuple[bool, str | None]:
        """Safety gate evaluation for simulated payload release."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                return False, f"Assignment {assignment_id} not found"

            # 1. Approach safety must be satisfied
            ok, reason = self.check_approach_safety(
                assignment_id,
                telemetry_override=telemetry_override,
                boundary=boundary,
            )
            if not ok:
                return False, f"Approach safety condition failed: {reason}"

            # 2. Exactly-once check
            if assignment.payload_released or assignment.release_id is not None:
                return False, f"Release already executed for assignment {assignment_id} (release_id: {assignment.release_id})"

            # 3. Standoff distance check (never drop directly on person)
            if assignment.standoff_distance_m is not None and assignment.standoff_distance_m < 1.0:
                return False, f"Standoff distance ({assignment.standoff_distance_m:.2f}m) < 1.0m; unsafe to release"

            # 4. Telemetry details: altitude and speed
            vid = assignment.servicer_id
            vehicles_dict = self.vehicles_state_supplier() if self.vehicles_state_supplier else {}
            vinfo = telemetry_override or vehicles_dict.get(vid, {})
            telem = vinfo.get("telemetry") or {}

            rel_alt = telem.get("relative_altitude_m")
            if rel_alt is not None:
                if abs(rel_alt - delivery_altitude_m) > altitude_tolerance_m:
                    return False, f"Altitude {rel_alt:.1f}m outside delivery envelope ({delivery_altitude_m} ± {altitude_tolerance_m}m)"

            speed = telem.get("ground_speed_m_s")
            if speed is not None and speed > max_ground_speed_m_s:
                return False, f"Ground speed {speed:.2f} m/s exceeds max release speed ({max_ground_speed_m_s} m/s)"

            # 5. Positional error to delivery standoff point
            if assignment.delivery_point is not None:
                deliv_e, deliv_n = assignment.delivery_point[0], assignment.delivery_point[1]
                veast = telem.get("east_m")
                vnorth = telem.get("north_m")
                if veast is not None and vnorth is not None:
                    pos_err = math.hypot(veast - deliv_e, vnorth - deliv_n)
                    if pos_err > max_positional_error_m:
                        return False, f"Positional error {pos_err:.2f}m exceeds tolerance ({max_positional_error_m}m)"

            return True, None

    def execute_simulated_release(
        self,
        assignment_id: UUID,
        *,
        telemetry_override: dict | None = None,
        boundary: Any = None,
    ) -> ServiceAssignment:
        """Safety-gated simulated payload release execution (strictly idempotent)."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state != AssignmentState.HOLDING_FOR_RELEASE:
                raise RuntimeError(
                    f"Cannot execute release in state {assignment.state}; must be HOLDING_FOR_RELEASE"
                )

            # Idempotency / Exactly-once gate
            if assignment.payload_released or assignment.release_id is not None:
                raise RuntimeError(
                    f"Exactly-once release violation: Payload already released for assignment {assignment_id}"
                )

            safe, reason = self.check_release_safety(
                assignment_id,
                telemetry_override=telemetry_override,
                boundary=boundary,
            )
            if not safe:
                raise RuntimeError(f"Simulated payload release blocked by safety gate: {reason}")

            now = datetime.now(UTC)
            release_id = f"rel_{assignment_id.hex[:8]}_{int(now.timestamp() * 1000)}"
            assignment.release_id = release_id
            assignment.payload_released = True

            vid = assignment.servicer_id
            vehicles_dict = self.vehicles_state_supplier() if self.vehicles_state_supplier else {}
            vinfo = telemetry_override or vehicles_dict.get(vid, {})
            telem = vinfo.get("telemetry") or {}

            evidence = {
                "release_id": release_id,
                "servicer_id": vid,
                "canonical_id": assignment.canonical_survivor_id,
                "released_at": now.isoformat(),
                "altitude_m": telem.get("relative_altitude_m", 5.0),
                "ground_speed_m_s": telem.get("ground_speed_m_s", 0.0),
                "standoff_distance_m": assignment.standoff_distance_m or 2.0,
                "delivery_point": assignment.delivery_point,
                "simulated": True,
            }
            assignment.delivery_evidence = evidence

            # Transition to RELEASED
            self.transition_assignment(
                assignment_id,
                AssignmentState.RELEASED,
                "Simulated payload release command accepted and logged",
                {"evidence": evidence},
            )

            # Immediately transition to VERIFYING
            return self.transition_assignment(
                assignment_id,
                AssignmentState.VERIFYING,
                "Payload released; initiating independent delivery verification",
            )

    def verify_delivery(
        self,
        assignment_id: UUID,
        *,
        verified: bool,
        evidence: dict | None = None,
        failure_reason: str | None = None,
    ) -> ServiceAssignment:
        """Independent delivery verification evaluation."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state != AssignmentState.VERIFYING:
                raise RuntimeError(
                    f"Cannot verify delivery in state {assignment.state}; must be VERIFYING"
                )

            if not assignment.payload_released:
                raise RuntimeError(
                    f"Cannot verify delivery for assignment {assignment_id}: Payload was never released"
                )

            if verified:
                assignment.delivery_verified = True
                if evidence and assignment.delivery_evidence:
                    assignment.delivery_evidence.update(evidence)
                elif evidence:
                    assignment.delivery_evidence = evidence

                self.registry.mark_delivered(assignment.canonical_survivor_id)

                return self.transition_assignment(
                    assignment_id,
                    AssignmentState.DELIVERED,
                    "Independent delivery verification criteria satisfied",
                    {"evidence": evidence},
                )
            else:
                assignment.delivery_verified = False
                fail_msg = failure_reason or "Delivery verification criteria failed or ambiguous"
                assignment.failure_reason = fail_msg

                prior_servicer = assignment.servicer_id
                if prior_servicer and self._servicer_to_assignment.get(prior_servicer) == assignment_id:
                    del self._servicer_to_assignment[prior_servicer]

                return self.transition_assignment(
                    assignment_id,
                    AssignmentState.FAILED,
                    f"Delivery verification failed: {fail_msg}",
                )

    def start_resuming_search(
        self,
        assignment_id: UUID,
        *,
        resume_seq: int | None = None,
    ) -> ServiceAssignment:
        """Transition assignment to RESUMING_SEARCH with saved search cursor."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state != AssignmentState.DELIVERED:
                raise RuntimeError(
                    f"Cannot resume search from state {assignment.state}; must be DELIVERED"
                )

            assignment.resume_seq = resume_seq
            return self.transition_assignment(
                assignment_id,
                AssignmentState.RESUMING_SEARCH,
                "Restoring saved search mission cursor and climbing to search altitude",
                {"resume_seq": resume_seq},
            )

    def complete_service(self, assignment_id: UUID) -> ServiceAssignment:
        """Complete service execution and release servicer reservation."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state not in {AssignmentState.RESUMING_SEARCH, AssignmentState.DELIVERED}:
                raise RuntimeError(
                    f"Cannot complete service from state {assignment.state}; must be RESUMING_SEARCH or DELIVERED"
                )

            result = self.transition_assignment(
                assignment_id,
                AssignmentState.COMPLETED,
                "Service job completed; search mission actively resumed",
            )

            # Release SERVICER reservation so it can accept next queued jobs
            prior_servicer = assignment.servicer_id
            if prior_servicer and self._servicer_to_assignment.get(prior_servicer) == assignment_id:
                del self._servicer_to_assignment[prior_servicer]

            self.reconcile_queue()
            return result

    def requeue_assignment(
        self,
        assignment_id: UUID,
        reason: str = "Pre-release failure clean requeue",
    ) -> ServiceAssignment:
        """Safely re-queue an assignment if and only if payload was not released."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.payload_released:
                raise RuntimeError(
                    f"Cannot re-queue assignment {assignment_id}: Payload was already released. "
                    "Post-release failures must fail closed without repeating release."
                )

            prior_servicer = assignment.servicer_id
            if prior_servicer and self._servicer_to_assignment.get(prior_servicer) == assignment_id:
                del self._servicer_to_assignment[prior_servicer]

            assignment.servicer_id = None
            assignment.reason = reason
            now = datetime.now(UTC)
            assignment.updated_at = now
            assignment.state = AssignmentState.QUEUED
            assignment.state_history.append({
                "from_state": "PRE_RELEASE_REQUEUE",
                "to_state": AssignmentState.QUEUED.value,
                "timestamp": now.isoformat(),
                "reason": reason,
            })

            self.registry.update_assignment(
                assignment.canonical_survivor_id,
                str(assignment_id),
                None,
                AssignmentState.QUEUED,
            )

            if self.event_callback:
                self.event_callback(
                    prior_servicer,
                    "JOB_REQUEUED",
                    f"Assignment {str(assignment_id)[:8]} re-queued: {reason}",
                    "WARNING",
                    {"assignment_id": str(assignment_id), "reason": reason},
                )

            self.reconcile_queue()
            return assignment.model_copy(deep=True)

    def cancel_assignment(self, assignment_id: UUID, reason: str = "Operator cancelled") -> ServiceAssignment:
        """Cancel an assignment."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            if assignment.state in {AssignmentState.CANCELLED, AssignmentState.FAILED, AssignmentState.COMPLETED}:
                return assignment.model_copy(deep=True)

            prior_servicer = assignment.servicer_id
            if prior_servicer and self._servicer_to_assignment.get(prior_servicer) == assignment_id:
                del self._servicer_to_assignment[prior_servicer]

            res = self.transition_assignment(
                assignment_id,
                AssignmentState.CANCELLED,
                reason,
            )
            self.reconcile_queue()
            return res

    def fail_assignment(self, assignment_id: UUID, reason: str = "Servicer failed") -> ServiceAssignment:
        """Mark an assignment as failed."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment {assignment_id} not found")

            prior_servicer = assignment.servicer_id
            if prior_servicer and self._servicer_to_assignment.get(prior_servicer) == assignment_id:
                del self._servicer_to_assignment[prior_servicer]

            assignment.failure_reason = reason
            res = self.transition_assignment(
                assignment_id,
                AssignmentState.FAILED,
                reason,
            )
            self.reconcile_queue()
            return res

    def reconcile_queue(self) -> list[ServiceAssignment]:
        """Re-evaluate any queued jobs against newly eligible SERVICERs."""
        reallocated = []
        with self._lock:
            queued_jobs = sorted(
                (a for a in self._assignments.values() if a.state == AssignmentState.QUEUED),
                key=self._job_order,
            )
            for job in queued_jobs:
                survivor = self.registry.get_survivor(job.canonical_survivor_id)
                if survivor is None:
                    continue
                eligible, reasons = self._find_eligible_servicers(survivor)
                best = self._select_best_servicer(eligible, survivor)
                if best is not None:
                    job.servicer_id = best
                    now = datetime.now(UTC)
                    job.state = AssignmentState.ASSIGNED
                    job.reason = f"Allocated to newly eligible servicer {best}"
                    job.updated_at = now
                    job.state_history.append({
                        "from_state": AssignmentState.QUEUED.value,
                        "to_state": AssignmentState.ASSIGNED.value,
                        "timestamp": now.isoformat(),
                        "reason": f"Allocated to newly eligible servicer {best}",
                    })
                    self._servicer_to_assignment[best] = job.assignment_id

                    self.registry.update_assignment(
                        job.canonical_survivor_id,
                        str(job.assignment_id),
                        best,
                        AssignmentState.ASSIGNED,
                    )
                    reallocated.append(job.model_copy(deep=True))

                    if self.event_callback:
                        self.event_callback(
                            best,
                            "JOB_ASSIGNED",
                            f"Queued job {str(job.assignment_id)[:8]} assigned to newly eligible {best}",
                            "INFO",
                            {"assignment_id": str(job.assignment_id), "servicer_id": best},
                        )
                else:
                    job.reason = "; ".join(reasons) if reasons else "No eligible SERVICER available"
        return reallocated

    def on_telemetry_or_state_change(self) -> list[ServiceAssignment]:
        """Hook called when telemetry or vehicle states update."""
        return self.reconcile_queue()

    def _find_eligible_servicers(
        self,
        survivor: SharedSurvivorRecord,
    ) -> tuple[list[str], list[str]]:
        """Identify SERVICERs that satisfy all eligibility criteria."""
        eligible: list[str] = []
        reasons: list[str] = []

        if survivor.delivery_status == "DELIVERED":
            return eligible, ["Survivor already has verified delivery"]
        if (datetime.now(UTC) - survivor.last_received_at).total_seconds() > MAX_QUEUED_TARGET_AGE_S:
            return eligible, ["Survivor observation stale; waiting for fresh camera reacquisition"]
        if ((survivor.latitude is None or survivor.longitude is None) and
                (survivor.east_m is None or survivor.north_m is None)):
            return eligible, ["Survivor location unavailable; waiting for valid observation"]
        if survivor.uncertainty_m is None or survivor.uncertainty_m > 8.0:
            return eligible, ["Survivor location uncertainty exceeds service safety limit"]

        vehicles_dict = self.vehicles_state_supplier() if self.vehicles_state_supplier else {}

        # Scan configured drones
        servicer_drones = [d for d in self.fleet_config.drones if d.role is DroneRole.SERVICER]
        if not servicer_drones:
            reasons.append("No SERVICER configured in fleet session")
            return eligible, reasons

        for drone in servicer_drones:
            vid = drone.vehicle_id

            # 1. Exclusive active job check
            active_job_id = self._servicer_to_assignment.get(vid)
            if active_job_id is not None:
                active_job = self._assignments.get(active_job_id)
                if active_job and (active_job.state in ACTIVE_SERVICE_STATES):
                    reasons.append(f"{vid} is already committed to active assignment {str(active_job_id)[:8]} ({active_job.state})")
                    continue

            # 2. Live telemetry & operational state check
            vinfo = vehicles_dict.get(vid)
            if vinfo is None:
                reasons.append(f"{vid} has no telemetry in current session")
                continue

            v_state = vinfo.get("state")
            if v_state not in {"READY", "SEARCHING"}:
                reasons.append(f"{vid} is in ineligible operational state '{v_state}'")
                continue

            # Telemetry freshness: connected and age <= 3.0s
            telem_age = vinfo.get("telemetry_age_s")
            connected = vinfo.get("connected", False)
            if telem_age is None or telem_age > 3.0 or not connected:
                age_str = f"{telem_age:.1f}s" if telem_age is not None else "unknown"
                reasons.append(f"{vid} telemetry is stale or disconnected (age: {age_str})")
                continue

            telem = vinfo.get("telemetry") or {}
            if telem.get("armed") is False or telem.get("flight_mode") in {"RTL", "LAND"}:
                reasons.append(f"{vid} is disarmed or returning")
                continue

            eligible.append(vid)

        return eligible, reasons

    def _select_best_servicer(
        self,
        eligible_servicers: list[str],
        survivor: SharedSurvivorRecord,
    ) -> str | None:
        """Deterministic selection policy:
        1. Distance to survivor if reliable position available for both.
        2. Vehicle ID lexicographical order.
        """
        if not eligible_servicers:
            return None

        vehicles_dict = self.vehicles_state_supplier() if self.vehicles_state_supplier else {}

        def sort_key(vid: str) -> tuple[float, str]:
            vinfo = vehicles_dict.get(vid, {})
            telem = vinfo.get("telemetry") or {}
            v_lat = telem.get("latitude")
            v_lon = telem.get("longitude")

            if (
                survivor.latitude is not None
                and survivor.longitude is not None
                and v_lat is not None
                and v_lon is not None
                and all(math.isfinite(float(x)) for x in (survivor.latitude, survivor.longitude, v_lat, v_lon))
            ):
                dlat = (v_lat - survivor.latitude) * 111_000.0
                dlon = (v_lon - survivor.longitude) * 111_000.0 * math.cos(math.radians(survivor.latitude))
                dist = math.hypot(dlat, dlon)
                return (dist, vid)

            # Missing vehicle position must not outrank a measurable route.
            return (float("inf"), vid)

        return min(eligible_servicers, key=sort_key)
