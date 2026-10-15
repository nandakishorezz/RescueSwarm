"""Sequenced launch/landing and pairwise separation monitoring."""

from __future__ import annotations

import math
import threading

from .models import FleetVehicle, SeparationAssessment, SeparationState, VehicleFleetState


class LaunchLandingScheduler:
    def __init__(self, vehicle_ids: list[str], departure_clear_altitude_m: float = 3):
        self.launch_queue = list(vehicle_ids)
        self.landing_queue: list[str] = []
        self.departure_clear_altitude_m = departure_clear_altitude_m
        self.active_launch: str | None = None
        self.active_landing: str | None = None

    def next_launch(self, vehicles: dict[str, FleetVehicle]) -> str | None:
        if self.active_launch:
            vehicle = vehicles[self.active_launch]
            if (vehicle.altitude_m or 0) < self.departure_clear_altitude_m:
                return None
            self.active_launch = None
        if not self.launch_queue:
            return None
        self.active_launch = self.launch_queue.pop(0)
        return self.active_launch

    def configure_landing(self, vehicle_ids: list[str]) -> None:
        self.landing_queue = list(vehicle_ids)

    def next_landing(self, vehicles: dict[str, FleetVehicle]) -> str | None:
        if self.active_landing:
            state = vehicles[self.active_landing].state
            if state not in {VehicleFleetState.LANDED, VehicleFleetState.FAILED, VehicleFleetState.UNAVAILABLE}:
                return None
            self.active_landing = None
        if not self.landing_queue:
            return None
        self.active_landing = self.landing_queue.pop(0)
        return self.active_landing


class CoordinatedLandingGate:
    """Thread-safe landing-slot gate used only by fleet execution.

    A vehicle whose search mission has completed holds at its final mission
    item until it owns the landing slot.  Emergency/failsafe RTL remains
    outside this gate: native/autonomy safety always takes precedence.
    """

    def __init__(self, vehicle_ids: list[str]):
        if not vehicle_ids or len(vehicle_ids) != len(set(vehicle_ids)):
            raise ValueError("landing queue requires unique vehicle IDs")
        self._queue = list(vehicle_ids)
        self._active: str | None = None
        self._completed: list[str] = []
        self._lock = threading.Lock()

    def try_acquire(self, vehicle_id: str) -> bool:
        with self._lock:
            if self._active == vehicle_id:
                return True
            if self._active is not None or not self._queue or self._queue[0] != vehicle_id:
                return False
            self._active = self._queue.pop(0)
            return True

    def complete(self, vehicle_id: str) -> None:
        with self._lock:
            if self._active != vehicle_id:
                raise ValueError(f"{vehicle_id} does not own the active landing slot")
            self._completed.append(vehicle_id)
            self._active = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "active": self._active,
                "queued": list(self._queue),
                "completed": list(self._completed),
            }


class FleetSeparationMonitor:
    def __init__(
        self,
        warning_horizontal_m: float,
        critical_horizontal_m: float,
        vertical_clear_m: float,
        max_sample_skew_s: float = 0.5,
    ):
        if not 0 < critical_horizontal_m < warning_horizontal_m:
            raise ValueError("critical horizontal separation must be below warning separation")
        if vertical_clear_m <= 0:
            raise ValueError("vertical_clear_m must be positive")
        if max_sample_skew_s <= 0:
            raise ValueError("max_sample_skew_s must be positive")
        self.warning_horizontal_m = warning_horizontal_m
        self.critical_horizontal_m = critical_horizontal_m
        self.vertical_clear_m = vertical_clear_m
        self.max_sample_skew_ns = int(max_sample_skew_s * 1_000_000_000)
        self.minimum_horizontal_m = float("inf")
        self.minimum_vertical_m = float("inf")
        self.warning_count = 0
        self.critical_count = 0
        self.skipped_stale_pairs = 0

    def assess(self, vehicles: list[FleetVehicle]) -> list[SeparationAssessment]:
        results: list[SeparationAssessment] = []
        positioned = [v for v in vehicles if v.east_m is not None and v.north_m is not None and v.altitude_m is not None]
        for index, left in enumerate(positioned):
            for right in positioned[index + 1:]:
                if (
                    left.last_update_monotonic_ns <= 0
                    or right.last_update_monotonic_ns <= 0
                    or abs(left.last_update_monotonic_ns - right.last_update_monotonic_ns) > self.max_sample_skew_ns
                ):
                    self.skipped_stale_pairs += 1
                    continue
                horizontal = math.hypot(left.east_m - right.east_m, left.north_m - right.north_m)
                vertical = abs(left.altitude_m - right.altitude_m)
                self.minimum_horizontal_m = min(self.minimum_horizontal_m, horizontal)
                self.minimum_vertical_m = min(self.minimum_vertical_m, vertical)
                if vertical >= self.vertical_clear_m:
                    state = SeparationState.CLEAR
                elif horizontal <= self.critical_horizontal_m:
                    state = SeparationState.CRITICAL
                    self.critical_count += 1
                elif horizontal <= self.warning_horizontal_m:
                    state = SeparationState.WARNING
                    self.warning_count += 1
                else:
                    state = SeparationState.CLEAR
                results.append(SeparationAssessment(
                    vehicle_a=left.vehicle_id,
                    vehicle_b=right.vehicle_id,
                    horizontal_distance_m=horizontal,
                    vertical_separation_m=vertical,
                    state=state,
                ))
        return results

    def metrics(self) -> dict[str, float | int | None]:
        return {
            "minimum_horizontal_separation_m": None if math.isinf(self.minimum_horizontal_m) else self.minimum_horizontal_m,
            "minimum_vertical_separation_m": None if math.isinf(self.minimum_vertical_m) else self.minimum_vertical_m,
            "separation_warnings": self.warning_count,
            "separation_critical_events": self.critical_count,
            "separation_stale_pairs_skipped": self.skipped_stale_pairs,
        }
