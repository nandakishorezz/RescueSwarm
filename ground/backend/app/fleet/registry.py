"""Independent vehicle freshness and mission-wide target deduplication."""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from rescue_core.geolocation.models import FusedTargetEstimate, TargetTrackState

from .models import CanonicalFleetTarget, FleetVehicle, VehicleFleetState


class DuplicateSystemIdError(ValueError):
    pass


class VehicleRegistry:
    def __init__(self, freshness_timeout_s: float = 3.0):
        if freshness_timeout_s <= 0:
            raise ValueError("freshness_timeout_s must be positive")
        self.freshness_timeout_ns = int(freshness_timeout_s * 1e9)
        self._vehicles: dict[str, FleetVehicle] = {}
        self._lock = threading.RLock()

    def register(self, vehicle: FleetVehicle) -> FleetVehicle:
        with self._lock:
            for existing in self._vehicles.values():
                if existing.system_id == vehicle.system_id and existing.vehicle_id != vehicle.vehicle_id:
                    raise DuplicateSystemIdError(
                        f"MAVLink SYSID {vehicle.system_id} is already registered to {existing.vehicle_id}"
                    )
            if vehicle.vehicle_id in self._vehicles:
                prior = self._vehicles[vehicle.vehicle_id]
                if prior.system_id != vehicle.system_id:
                    raise DuplicateSystemIdError(f"{vehicle.vehicle_id} changed SYSID")
            self._vehicles[vehicle.vehicle_id] = vehicle
            return vehicle

    def update(self, vehicle_id: str, **values: object) -> FleetVehicle:
        with self._lock:
            vehicle = self._vehicles[vehicle_id]
            if "vehicle_sequence" in values and int(values["vehicle_sequence"]) <= vehicle.vehicle_sequence:
                return vehicle
            for name, value in values.items():
                if not hasattr(vehicle, name):
                    raise ValueError(f"Unknown vehicle field: {name}")
                setattr(vehicle, name, value)
            vehicle.last_update_monotonic_ns = time.monotonic_ns()
            return vehicle

    def get(self, vehicle_id: str) -> FleetVehicle:
        return self._vehicles[vehicle_id]

    def all(self) -> list[FleetVehicle]:
        return list(self._vehicles.values())

    def is_fresh(self, vehicle_id: str, now_ns: int | None = None) -> bool:
        vehicle = self._vehicles[vehicle_id]
        now_ns = now_ns or time.monotonic_ns()
        return vehicle.connected and vehicle.last_update_monotonic_ns > 0 and (
            now_ns - vehicle.last_update_monotonic_ns <= self.freshness_timeout_ns
        )

    def eligible_for_work(self, vehicle_id: str, now_ns: int | None = None) -> bool:
        vehicle = self._vehicles[vehicle_id]
        return (
            self.is_fresh(vehicle_id, now_ns)
            and not vehicle.native_failsafe
            and vehicle.state not in {
                VehicleFleetState.RETURNING, VehicleFleetState.LANDING, VehicleFleetState.LANDED,
                VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE,
            }
        )

    def snapshot(self) -> dict[str, object]:
        return {key: value.model_dump(mode="json") for key, value in sorted(self._vehicles.items())}


class SharedTargetRegistry:
    """Merges confirmed Stage 5 estimates without losing per-vehicle provenance."""

    def __init__(self, mission_id: str, association_radius_m: float = 6.0, uncertainty_floor_m: float = 1.0):
        if association_radius_m <= 0 or uncertainty_floor_m <= 0:
            raise ValueError("Target registry thresholds must be positive")
        self.mission_id = mission_id
        self.association_radius_m = association_radius_m
        self.uncertainty_floor_m = uncertainty_floor_m
        self._targets: dict[str, CanonicalFleetTarget] = {}
        self._source_target_ids: dict[str, str] = {}
        self._samples: dict[str, list[tuple[str, FusedTargetEstimate]]] = {}
        self._lock = threading.RLock()
        self.duplicate_merges = 0

    def ingest(self, vehicle_id: str, estimate: FusedTargetEstimate) -> CanonicalFleetTarget:
        if estimate.state is not TargetTrackState.CONFIRMED:
            raise ValueError("Only confirmed Stage 5 targets enter the shared confirmed registry")
        source_key = f"{vehicle_id}:{estimate.target_id}"
        with self._lock:
            existing_id = self._source_target_ids.get(source_key)
            if existing_id:
                return self._targets[existing_id]
            canonical = self._associate(estimate)
            if canonical is None:
                canonical_id = str(uuid5(
                    NAMESPACE_URL, f"{self.mission_id}:fleet-target:{len(self._targets) + 1}"
                ))
                canonical = CanonicalFleetTarget(
                    target_id=canonical_id,
                    mission_id=self.mission_id,
                    east_m=estimate.east_m,
                    north_m=estimate.north_m,
                    latitude=estimate.latitude,
                    longitude=estimate.longitude,
                    uncertainty_m=max(estimate.uncertainty_m, self.uncertainty_floor_m),
                    observing_vehicle_ids=[vehicle_id],
                    observation_ids=list(estimate.inlier_observation_ids),
                    evidence_references=list(estimate.evidence_references),
                    detection_confidence=estimate.detection_confidence,
                    first_seen_at=estimate.first_seen_at,
                    last_seen_at=estimate.last_seen_at,
                    simulated=estimate.simulated,
                )
                self._targets[canonical_id] = canonical
                self._samples[canonical_id] = []
            else:
                self.duplicate_merges += 1
            self._source_target_ids[source_key] = canonical.target_id
            self._samples[canonical.target_id].append((vehicle_id, estimate))
            self._refuse_if_delivered(canonical)
            self._refit(canonical)
            return canonical

    def _associate(self, estimate: FusedTargetEstimate) -> CanonicalFleetTarget | None:
        candidates = []
        for target in self._targets.values():
            distance = math.hypot(estimate.east_m - target.east_m, estimate.north_m - target.north_m)
            gate = max(
                self.association_radius_m,
                math.hypot(estimate.uncertainty_m, target.uncertainty_m),
            )
            if distance <= gate:
                candidates.append((distance, target))
        return min(candidates, key=lambda pair: pair[0])[1] if candidates else None

    @staticmethod
    def _refuse_if_delivered(target: CanonicalFleetTarget) -> None:
        if target.delivery_state == "DELIVERED" and target.assignment_id is None:
            # Delivered provenance can survive cleanup, but its state can never be reset.
            target.delivery_state = "DELIVERED"

    def _refit(self, target: CanonicalFleetTarget) -> None:
        samples = self._samples[target.target_id]
        weights = [1 / max(item.uncertainty_m, self.uncertainty_floor_m) ** 2 for _, item in samples]
        total = sum(weights)
        target.east_m = sum(item.east_m * weight for (_, item), weight in zip(samples, weights, strict=True)) / total
        target.north_m = sum(item.north_m * weight for (_, item), weight in zip(samples, weights, strict=True)) / total
        target.latitude = sum(item.latitude * weight for (_, item), weight in zip(samples, weights, strict=True)) / total
        target.longitude = sum(item.longitude * weight for (_, item), weight in zip(samples, weights, strict=True)) / total
        # Preserve a common-bias floor; more M8N-like observations do not imply centimetres.
        target.uncertainty_m = max(
            self.uncertainty_floor_m,
            max(item.uncertainty_m for _, item in samples) / math.sqrt(len(samples)),
        )
        target.detection_confidence = sum(item.detection_confidence for _, item in samples) / len(samples)
        target.observing_vehicle_ids = sorted({vehicle for vehicle, _ in samples})
        target.observation_ids = sorted({value for _, item in samples for value in item.inlier_observation_ids})
        target.evidence_references = sorted({value for _, item in samples for value in item.evidence_references})
        target.first_seen_at = min(item.first_seen_at for _, item in samples)
        target.last_seen_at = max(item.last_seen_at for _, item in samples)
        target.simulated = all(item.simulated for _, item in samples)

    def get(self, target_id: str) -> CanonicalFleetTarget:
        return self._targets[target_id]

    def all(self) -> list[CanonicalFleetTarget]:
        return list(self._targets.values())

    def pending(self) -> list[CanonicalFleetTarget]:
        return [item for item in self._targets.values() if item.delivery_state == "PENDING"]

    def mark_delivered(self, target_id: str) -> None:
        self._targets[target_id].delivery_state = "DELIVERED"

    def snapshot(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in self.all()]
