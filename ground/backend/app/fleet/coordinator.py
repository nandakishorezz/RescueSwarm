"""Fleet authority: who searches where and who services which target."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .allocation import AssignmentLeaseManager, DeliveryAllocator, SectorAssignmentManager
from .models import (
    AssignmentLeaseState,
    FleetPayloadCapacity,
    FleetPlan,
    FleetRecoverySnapshot,
    FleetState,
    VehicleFleetState,
)
from .registry import SharedTargetRegistry, VehicleRegistry
from .safety import AuthorizedFleetBroadcast, FleetCommandRouter, FleetInvariantChecker


class FleetStateError(RuntimeError):
    pass


class FleetMissionCoordinator:
    """Coordinates frozen vehicle agents; contains no raw MAVLink operations."""

    _ALLOWED = {
        FleetState.IDLE: {FleetState.PREPARING},
        FleetState.PREPARING: {FleetState.PARTITIONING, FleetState.FAILED},
        FleetState.PARTITIONING: {FleetState.READY, FleetState.FAILED},
        FleetState.READY: {FleetState.LAUNCHING, FleetState.ABORTED},
        FleetState.LAUNCHING: {FleetState.SEARCHING, FleetState.RECALLING, FleetState.FAILED},
        FleetState.SEARCHING: {FleetState.SERVICING_TARGETS, FleetState.REALLOCATING, FleetState.RECALLING, FleetState.RETURNING, FleetState.FAILED},
        FleetState.SERVICING_TARGETS: {FleetState.SEARCHING, FleetState.REALLOCATING, FleetState.RECALLING, FleetState.RETURNING, FleetState.FAILED},
        FleetState.REALLOCATING: {FleetState.SEARCHING, FleetState.RECALLING, FleetState.FAILED},
        FleetState.RECALLING: {FleetState.RETURNING, FleetState.LANDING, FleetState.FAILED},
        FleetState.RETURNING: {FleetState.LANDING, FleetState.FAILED},
        FleetState.LANDING: {FleetState.COMPLETED, FleetState.FAILED},
    }

    def __init__(
        self,
        plan: FleetPlan,
        vehicles: VehicleRegistry,
        targets: SharedTargetRegistry,
        command_router: FleetCommandRouter | None = None,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.plan = plan
        self.vehicles = vehicles
        self.targets = targets
        self.command_router = command_router
        self.events = event_callback or (lambda _event, _metadata: None)
        self.state = FleetState.IDLE
        self.leases = AssignmentLeaseManager()
        self.sectors = SectorAssignmentManager(plan.assignments)
        self.allocator = DeliveryAllocator(vehicles, self.leases)
        self.invariants = FleetInvariantChecker()
        self.completed_segments: set[str] = set()
        starting_payloads = {
            vehicle: vehicles.get(vehicle).payload_remaining for vehicle in plan.vehicle_ids
        }
        self.payload_capacity = FleetPayloadCapacity(
            starting_by_vehicle=dict(starting_payloads),
            remaining_by_vehicle=dict(starting_payloads),
        )
        self.payload_inventory = self.payload_capacity.remaining_by_vehicle
        self._fleet_sequence = 0
        self._lock = threading.RLock()

    def transition(self, state: FleetState, reason: str) -> None:
        with self._lock:
            if state not in self._ALLOWED.get(self.state, set()):
                raise FleetStateError(f"Invalid fleet transition {self.state} -> {state}")
            prior = self.state
            self.state = state
            self._emit("FLEET_STATE_CHANGED", {"prior": prior, "state": state, "reason": reason})

    def prepare(self) -> None:
        self.transition(FleetState.PREPARING, "One operator mission start")
        self.transition(FleetState.PARTITIONING, "Global coverage plan partitioned")
        if set(self.plan.vehicle_ids) != {item.vehicle_id for item in self.vehicles.all()}:
            raise FleetStateError("All configured vehicles must register before READY")
        if not all(self.vehicles.eligible_for_work(vehicle) for vehicle in self.plan.vehicle_ids):
            raise FleetStateError("All configured fleet vehicles must be fresh and healthy")
        self.transition(FleetState.READY, "Fleet plan and unique vehicle identities verified")

    def start(self) -> None:
        if self.state is not FleetState.READY:
            raise FleetStateError("Fleet must be READY before launch")
        self.transition(FleetState.LAUNCHING, "Coordinated launch scheduled")

    def parallel_search_started(self) -> None:
        self.transition(FleetState.SEARCHING, "All launched vehicles entered parallel AUTO search")
        self._emit("PARALLEL_SEARCH_STARTED", {})

    def allocate_pending_targets(self) -> list[dict[str, Any]]:
        allocations = []
        for target in sorted(self.targets.pending(), key=lambda item: (item.first_seen_at, item.target_id)):
            lease = self.allocator.allocate(target)
            if lease:
                allocations.append(lease.model_dump(mode="json"))
                self._emit("DELIVERY_ASSIGNED", allocations[-1])
        if allocations and self.state is FleetState.SEARCHING:
            self.transition(FleetState.SERVICING_TARGETS, "One or more delivery leases active")
        return allocations

    def complete_delivery(self, assignment_id: str, verified: bool, ambiguous: bool = False) -> None:
        lease = next(item for item in self.leases.all() if item.assignment_id == assignment_id)
        if ambiguous:
            self.leases.transition(assignment_id, AssignmentLeaseState.AMBIGUOUS)
            self.targets.get(lease.target_id).delivery_state = "AMBIGUOUS"
        elif verified:
            self.leases.transition(assignment_id, AssignmentLeaseState.COMPLETED)
            self.targets.mark_delivered(lease.target_id)
            vehicle = self.vehicles.get(lease.vehicle_id)
            vehicle.payload_remaining -= 1
            self.payload_inventory[lease.vehicle_id] = vehicle.payload_remaining
            self.payload_capacity.released += 1
            self.payload_capacity.verified += 1
        else:
            self.leases.transition(assignment_id, AssignmentLeaseState.FAILED)
            self.targets.get(lease.target_id).delivery_state = "PENDING"
        self.vehicles.get(lease.vehicle_id).current_target_id = None
        self._emit("DELIVERY_COMPLETED" if verified else "DELIVERY_FAILED", {
            "assignment_id": assignment_id, "verified": verified, "ambiguous": ambiguous,
        })

    def vehicle_failed(self, vehicle_id: str) -> list[str]:
        vehicle = self.vehicles.get(vehicle_id)
        vehicle.state = VehicleFleetState.FAILED
        vehicle.native_failsafe = True
        if self.state in {FleetState.SEARCHING, FleetState.SERVICING_TARGETS}:
            self.transition(FleetState.REALLOCATING, f"{vehicle_id} failed; preserving unfinished work")
        for lease in self.leases.all():
            if lease.vehicle_id == vehicle_id and lease.state in {
                AssignmentLeaseState.ASSIGNED, AssignmentLeaseState.ACCEPTED,
            }:
                self.leases.transition(lease.assignment_id, AssignmentLeaseState.FAILED)
                self.leases.transition(lease.assignment_id, AssignmentLeaseState.RELEASED)
                target = self.targets.get(lease.target_id)
                target.delivery_state = "PENDING"
                target.assigned_vehicle_id = None
                target.assignment_id = None
                vehicle.current_target_id = None
            elif lease.vehicle_id == vehicle_id and lease.state is AssignmentLeaseState.ACTIVE:
                self.leases.transition(lease.assignment_id, AssignmentLeaseState.AMBIGUOUS)
                self.targets.get(lease.target_id).delivery_state = "AMBIGUOUS"
        unfinished = self.sectors.mark_vehicle_failed(vehicle_id, self.completed_segments)
        self._emit("VEHICLE_FAILED", {"vehicle_id": vehicle_id, "unfinished_segments": unfinished})
        return unfinished

    def reassign_coverage(self, segment_ids: list[str]) -> str | None:
        candidates = [
            vehicle for vehicle in self.vehicles.all()
            if self.vehicles.eligible_for_work(vehicle.vehicle_id)
        ]
        if not candidates:
            self._emit("MISSION_CAPACITY_DEGRADED", {"segments": segment_ids})
            return None
        winner = min(candidates, key=lambda vehicle: (vehicle.search_progress_percent, vehicle.vehicle_id))
        assignment = self.sectors.reassign(segment_ids, winner.vehicle_id)
        self._emit("COVERAGE_REASSIGNED", assignment.model_dump(mode="json"))
        self.transition(FleetState.SEARCHING, "Recovery coverage assigned to healthy vehicle")
        return winner.vehicle_id

    def emergency_recall(self) -> dict[str, Any]:
        if self.command_router is None:
            raise FleetStateError("Fleet recall has no command router")
        if self.state not in {FleetState.RECALLING, FleetState.RETURNING, FleetState.LANDING}:
            self.transition(FleetState.RECALLING, "One operator emergency recall")
        results = self.command_router.broadcast(AuthorizedFleetBroadcast("EMERGENCY_RECALL"), "RTL")
        self._emit("FLEET_RECALL_REQUESTED", {"vehicles": sorted(results)})
        return results

    def snapshot(self) -> FleetRecoverySnapshot:
        all_segments = {segment.segment_id for segment in self.plan.segments}
        return FleetRecoverySnapshot(
            mission_fingerprint=self.plan.mission_fingerprint,
            fleet_plan=self.plan.model_dump(mode="json"),
            vehicle_registry=self.vehicles.snapshot(),
            sector_assignments=[item.model_dump(mode="json") for item in self.sectors.assignments.values()],
            completed_segment_ids=sorted(self.completed_segments),
            uncompleted_segment_ids=sorted(all_segments - self.completed_segments),
            target_registry=self.targets.snapshot(),
            assignment_leases=[item.model_dump(mode="json") for item in self.leases.all()],
            payload_inventory=self.payload_inventory,
            launch_landing_schedule={},
            fleet_state=self.state,
        )

    def persist_snapshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(self.snapshot().model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)

    def _emit(self, event: str, metadata: dict[str, Any]) -> None:
        self._fleet_sequence += 1
        self.events(event, {
            "fleet_sequence": self._fleet_sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            **metadata,
        })
