"""Transactional delivery and coverage assignment for a deterministic fleet."""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime, timedelta

from .models import (
    AssignmentLease,
    AssignmentLeaseState,
    CanonicalFleetTarget,
    CoverageWorkState,
    FleetVehicle,
    SectorAssignment,
)
from .registry import VehicleRegistry


ACTIVE_LEASE_STATES = {
    AssignmentLeaseState.PROPOSED,
    AssignmentLeaseState.ASSIGNED,
    AssignmentLeaseState.ACCEPTED,
    AssignmentLeaseState.ACTIVE,
}


class AssignmentConflict(RuntimeError):
    pass


class AssignmentLeaseManager:
    def __init__(self):
        self._leases: dict[str, AssignmentLease] = {}
        self._lock = threading.RLock()

    def acquire(self, target_id: str, vehicle_id: str, payload_slot: str | None = None, ttl_s: float = 60) -> AssignmentLease:
        with self._lock:
            existing = self.active_for_target(target_id)
            if existing:
                if existing.vehicle_id == vehicle_id:
                    return existing
                raise AssignmentConflict(f"Target {target_id} already leased to {existing.vehicle_id}")
            if payload_slot is not None:
                occupied = next(
                    (
                        lease for lease in self._leases.values()
                        if lease.payload_slot == payload_slot and lease.state in ACTIVE_LEASE_STATES
                    ),
                    None,
                )
                if occupied:
                    raise AssignmentConflict(
                        f"Payload slot {payload_slot} is already assigned to {occupied.target_id}"
                    )
            version = 1 + max(
                (lease.assignment_version for lease in self._leases.values() if lease.target_id == target_id),
                default=0,
            )
            lease = AssignmentLease(
                target_id=target_id,
                vehicle_id=vehicle_id,
                payload_slot=payload_slot,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
                assignment_version=version,
                state=AssignmentLeaseState.ASSIGNED,
            )
            self._leases[lease.assignment_id] = lease
            return lease

    def transition(self, assignment_id: str, state: AssignmentLeaseState) -> AssignmentLease:
        with self._lock:
            lease = self._leases[assignment_id]
            allowed = {
                AssignmentLeaseState.ASSIGNED: {AssignmentLeaseState.ACCEPTED, AssignmentLeaseState.FAILED, AssignmentLeaseState.RELEASED},
                AssignmentLeaseState.ACCEPTED: {AssignmentLeaseState.ACTIVE, AssignmentLeaseState.FAILED, AssignmentLeaseState.RELEASED},
                AssignmentLeaseState.ACTIVE: {AssignmentLeaseState.COMPLETED, AssignmentLeaseState.FAILED, AssignmentLeaseState.AMBIGUOUS},
                AssignmentLeaseState.FAILED: {AssignmentLeaseState.RELEASED},
            }
            if state == lease.state:
                return lease
            if state not in allowed.get(lease.state, set()):
                raise AssignmentConflict(f"Invalid lease transition {lease.state} -> {state}")
            lease.state = state
            return lease

    def active_for_target(self, target_id: str) -> AssignmentLease | None:
        active = [item for item in self._leases.values() if item.target_id == target_id and item.state in ACTIVE_LEASE_STATES]
        if len(active) > 1:
            raise AssignmentConflict(f"Invariant violated: multiple active leases for {target_id}")
        return active[0] if active else None

    def all(self) -> list[AssignmentLease]:
        return list(self._leases.values())

    def restore(self, leases: list[AssignmentLease]) -> None:
        with self._lock:
            self._leases = {lease.assignment_id: lease for lease in leases}
            for target_id in {lease.target_id for lease in leases}:
                self.active_for_target(target_id)


class DeliveryAllocator:
    """Auditable completion-time heuristic; eligibility is evaluated first."""

    def __init__(
        self,
        registry: VehicleRegistry,
        leases: AssignmentLeaseManager,
        cruise_speed_m_s: float = 5,
        minimum_battery_percent: float = 25,
        maximum_uncertainty_m: float = 15,
    ):
        self.registry, self.leases = registry, leases
        self.cruise_speed_m_s = cruise_speed_m_s
        self.minimum_battery_percent = minimum_battery_percent
        self.maximum_uncertainty_m = maximum_uncertainty_m
        self._lock = threading.RLock()

    def allocate(self, target: CanonicalFleetTarget) -> AssignmentLease | None:
        with self._lock:
            if target.delivery_state in {"DELIVERED", "AMBIGUOUS"}:
                return None
            existing = self.leases.active_for_target(target.target_id)
            if existing:
                return existing
            if target.uncertainty_m > self.maximum_uncertainty_m:
                return None
            candidates: list[tuple[float, str]] = []
            for vehicle in self.registry.all():
                if not self._eligible(vehicle):
                    continue
                distance = math.hypot((vehicle.east_m or 0) - target.east_m, (vehicle.north_m or 0) - target.north_m)
                travel = distance / max(self.cruise_speed_m_s, 0.1)
                workload = vehicle.search_progress_percent / 100
                battery_penalty = (100 - (vehicle.battery_percent or 0)) / 100
                candidates.append((travel + workload + battery_penalty, vehicle.vehicle_id))
            if not candidates:
                return None
            _, winner = min(candidates, key=lambda value: (round(value[0], 9), value[1]))
            lease = self.leases.acquire(target.target_id, winner)
            vehicle = self.registry.get(winner)
            vehicle.current_target_id = target.target_id
            target.assigned_vehicle_id = winner
            target.assignment_id = lease.assignment_id
            target.assignment_version = lease.assignment_version
            target.delivery_state = "ASSIGNED"
            return lease

    def _eligible(self, vehicle: FleetVehicle) -> bool:
        return (
            self.registry.eligible_for_work(vehicle.vehicle_id)
            and vehicle.payload_remaining > 0
            and vehicle.gps_healthy is not False
            and vehicle.ekf_healthy is not False
            and (vehicle.battery_percent is None or vehicle.battery_percent >= self.minimum_battery_percent)
            and not vehicle.current_target_id
        )


class SectorAssignmentManager:
    def __init__(self, assignments: list[SectorAssignment]):
        self.assignments = {item.assignment_id: item for item in assignments}
        self._segment_owner: dict[str, str] = {}
        self._lock = threading.RLock()
        for assignment in assignments:
            self._claim(assignment)

    def _claim(self, assignment: SectorAssignment) -> None:
        for segment_id in assignment.segment_ids:
            if segment_id in self._segment_owner:
                raise AssignmentConflict(f"Coverage segment {segment_id} has multiple owners")
            self._segment_owner[segment_id] = assignment.vehicle_id

    def mark_vehicle_failed(self, vehicle_id: str, completed_segment_ids: set[str]) -> list[str]:
        with self._lock:
            remaining: list[str] = []
            for assignment in self.assignments.values():
                if assignment.vehicle_id != vehicle_id or assignment.state is CoverageWorkState.COMPLETED:
                    continue
                assignment.state = CoverageWorkState.REASSIGN_REQUIRED
                assignment.version += 1
                for segment_id in assignment.segment_ids:
                    if segment_id not in completed_segment_ids:
                        self._segment_owner.pop(segment_id, None)
                        remaining.append(segment_id)
            return remaining

    def reassign(self, segment_ids: list[str], vehicle_id: str) -> SectorAssignment:
        with self._lock:
            for segment_id in segment_ids:
                if segment_id in self._segment_owner:
                    raise AssignmentConflict(f"Coverage segment {segment_id} is already assigned")
            assignment = SectorAssignment(
                vehicle_id=vehicle_id,
                segment_ids=segment_ids,
                estimated_cost=0,
                estimated_search_time_s=0,
            )
            self.assignments[assignment.assignment_id] = assignment
            self._claim(assignment)
            return assignment

    def owner(self, segment_id: str) -> str | None:
        return self._segment_owner.get(segment_id)
