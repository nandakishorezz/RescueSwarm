"""Fleet invariants and fail-closed command targeting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..sitl.guard import SITLSafetyGuard
from .allocation import ACTIVE_LEASE_STATES, AssignmentLeaseManager
from .models import FleetState, VehicleFleetState
from .registry import VehicleRegistry


class FleetInvariantViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorizedFleetBroadcast:
    operation: str

    def __post_init__(self) -> None:
        if self.operation not in {"EMERGENCY_RECALL", "SAFETY_ABORT"}:
            raise PermissionError("Only fleet safety recall/abort may broadcast")


class FleetCommandRouter:
    """Never turns target_system=0 into an accidental command broadcast."""

    def __init__(self, registry: VehicleRegistry, senders: dict[str, Callable[..., Any]]):
        self.registry, self.senders = registry, senders

    def send_vehicle(self, vehicle_id: str, command: str, **parameters: object) -> Any:
        if not vehicle_id or vehicle_id not in self.senders:
            raise PermissionError("Single-vehicle command requires an explicit registered vehicle ID")
        vehicle = self.registry.get(vehicle_id)
        if vehicle.system_id == 0:
            raise PermissionError("Broadcast target_system=0 is forbidden for vehicle commands")
        return self.senders[vehicle_id](
            command=command,
            target_system=vehicle.system_id,
            target_component=1,
            **parameters,
        )

    def broadcast(self, authorization: AuthorizedFleetBroadcast, command: str) -> dict[str, Any]:
        if command != "RTL":
            raise PermissionError("Fleet safety broadcast currently permits RTL only")
        return {
            vehicle.vehicle_id: self.send_vehicle(vehicle.vehicle_id, command)
            for vehicle in self.registry.all()
            if vehicle.state not in {VehicleFleetState.LANDED, VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE}
        }


class FleetInvariantChecker:
    def check(
        self,
        registry: VehicleRegistry,
        leases: AssignmentLeaseManager,
        segment_owners: dict[str, str],
        payload_inventory: dict[str, int],
        delivered_targets: set[str],
        fleet_state: FleetState,
    ) -> list[str]:
        violations: list[str] = []
        vehicles = registry.all()
        if len({v.vehicle_id for v in vehicles}) != len(vehicles):
            violations.append("vehicle_id uniqueness violated")
        if len({v.system_id for v in vehicles}) != len(vehicles):
            violations.append("MAVLink SYSID uniqueness violated")
        active_by_target: dict[str, int] = {}
        active_by_payload_slot: dict[str, int] = {}
        for lease in leases.all():
            if lease.state in ACTIVE_LEASE_STATES:
                active_by_target[lease.target_id] = active_by_target.get(lease.target_id, 0) + 1
                if lease.payload_slot:
                    active_by_payload_slot[lease.payload_slot] = active_by_payload_slot.get(lease.payload_slot, 0) + 1
                if lease.target_id in delivered_targets:
                    violations.append(f"Delivered target {lease.target_id} has an active lease")
                vehicle = registry.get(lease.vehicle_id)
                if vehicle.native_failsafe or vehicle.state in {VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE}:
                    violations.append(f"Failed/failsafe vehicle {lease.vehicle_id} has active work")
        if any(count > 1 for count in active_by_target.values()):
            violations.append("Target has multiple active delivery leases")
        if any(count > 1 for count in active_by_payload_slot.values()):
            violations.append("Payload slot has multiple active assignments")
        if any(value < 0 for value in payload_inventory.values()):
            violations.append("Fleet payload inventory is negative")
        if len(segment_owners) != len(set(segment_owners)):
            violations.append("Coverage segment has multiple active owners")
        if fleet_state is FleetState.COMPLETED and any(
            vehicle.state not in {VehicleFleetState.LANDED, VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE}
            for vehicle in vehicles
        ):
            violations.append("Fleet completed before all vehicles reached safe terminal state")
        return violations

    def require(self, *args: Any, **kwargs: Any) -> None:
        violations = self.check(*args, **kwargs)
        if violations:
            raise FleetInvariantViolation("; ".join(violations))


def guarded_fleet_connections(connections: dict[str, str]) -> dict[str, str]:
    guard = SITLSafetyGuard("sitl", "simulation_only")
    if len(connections) != len(set(connections.values())):
        raise PermissionError("Each fleet vehicle requires a distinct SITL endpoint")
    return {vehicle: guard.validate(endpoint).connection_string for vehicle, endpoint in connections.items()}
