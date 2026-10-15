"""Target queueing and bounded diversion eligibility policies."""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from shapely.geometry import Point, Polygon

from rescue_core.geolocation.models import FusedTargetEstimate, TargetTrackState


class EligibilityReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    DELIVERY_NOT_REQUIRED = "DELIVERY_NOT_REQUIRED"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"
    ALREADY_SERVICED = "ALREADY_SERVICED"
    UNCERTAINTY_TOO_HIGH = "UNCERTAINTY_TOO_HIGH"
    OUTSIDE_OPERATIONAL_REGION = "OUTSIDE_OPERATIONAL_REGION"
    PAYLOAD_UNAVAILABLE = "PAYLOAD_UNAVAILABLE"
    VEHICLE_UNHEALTHY = "VEHICLE_UNHEALTHY"
    BATTERY_TOO_LOW = "BATTERY_TOO_LOW"
    DEADLINE_RESERVE = "DEADLINE_RESERVE"
    INSUFFICIENT_SURVIVOR_EVIDENCE = "INSUFFICIENT_SURVIVOR_EVIDENCE"


class SurvivorEligibilityState(StrEnum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SERVICE_CANDIDATE = "SERVICE_CANDIDATE"


class SurvivorEligibilityClassifier:
    """Conservative evidence layer distinguishing detected persons from rescue-service candidates.
    Does NOT attempt to diagnose medical conditions. Evaluates available perception evidence."""
    
    def __init__(
        self,
        min_inlier_count: int = 3,
        max_uncertainty_m: float = 8.0,
        min_detection_confidence: float = 0.25,
    ):
        self.min_inlier_count = min_inlier_count
        self.max_uncertainty_m = max_uncertainty_m
        self.min_detection_confidence = min_detection_confidence
        
    def evaluate(self, target: FusedTargetEstimate) -> SurvivorEligibilityState:
        # Check temporal persistence (minimum repeated reliable observations)
        if target.inlier_count < self.min_inlier_count:
            return SurvivorEligibilityState.INSUFFICIENT_EVIDENCE
            
        # Check spatial immobility / consistency
        # High uncertainty usually implies a moving person or extreme noise
        if target.uncertainty_m > self.max_uncertainty_m:
            return SurvivorEligibilityState.NOT_ELIGIBLE
            
        # Check vision confidence consistency
        if target.detection_confidence < self.min_detection_confidence:
            return SurvivorEligibilityState.INSUFFICIENT_EVIDENCE
            
        return SurvivorEligibilityState.SERVICE_CANDIDATE


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason: EligibilityReason


class InventoryView(Protocol):
    @property
    def remaining(self) -> int: ...


class TargetEligibilityPolicy:
    def __init__(
        self, operational_region: Polygon, maximum_uncertainty_m: float,
        minimum_battery_percent: float, delivery_time_reserve_s: float,
        min_detection_confidence: float = 0.25,
    ):
        self.region = operational_region
        self.maximum_uncertainty_m = maximum_uncertainty_m
        self.minimum_battery_percent = minimum_battery_percent
        self.delivery_time_reserve_s = delivery_time_reserve_s
        # The underlying tracker confirms at 3, matching the service layer requirement.
        self.survivor_classifier = SurvivorEligibilityClassifier(
            min_inlier_count=3,
            max_uncertainty_m=maximum_uncertainty_m,
            min_detection_confidence=min_detection_confidence,
        )

    def evaluate(
        self, target: FusedTargetEstimate, inventory: InventoryView, *,
        delivery_required: bool = True, already_serviced: bool = False,
        vehicle_healthy: bool = True, battery_percent: float = 100,
        remaining_mission_time_s: float = float("inf"),
    ) -> EligibilityDecision:
        if target.state is not TargetTrackState.CONFIRMED:
            return EligibilityDecision(False, EligibilityReason.NOT_CONFIRMED)
        if not delivery_required:
            return EligibilityDecision(False, EligibilityReason.DELIVERY_NOT_REQUIRED)
        if target.delivery_state in {"DELIVERED", "DELIVERED_VERIFIED"}:
            return EligibilityDecision(False, EligibilityReason.ALREADY_DELIVERED)
        if already_serviced:
            return EligibilityDecision(False, EligibilityReason.ALREADY_SERVICED)
        if target.uncertainty_m > self.maximum_uncertainty_m:
            return EligibilityDecision(False, EligibilityReason.UNCERTAINTY_TOO_HIGH)
        if not self.region.buffer(1e-6).covers(Point(target.east_m, target.north_m)):
            return EligibilityDecision(False, EligibilityReason.OUTSIDE_OPERATIONAL_REGION)
        if inventory.remaining < 1:
            return EligibilityDecision(False, EligibilityReason.PAYLOAD_UNAVAILABLE)
        if not vehicle_healthy:
            return EligibilityDecision(False, EligibilityReason.VEHICLE_UNHEALTHY)
        if battery_percent < self.minimum_battery_percent:
            return EligibilityDecision(False, EligibilityReason.BATTERY_TOO_LOW)
        if remaining_mission_time_s < self.delivery_time_reserve_s:
            return EligibilityDecision(False, EligibilityReason.DEADLINE_RESERVE)

        survivor_state = self.survivor_classifier.evaluate(target)
        if survivor_state != SurvivorEligibilityState.SERVICE_CANDIDATE:
            return EligibilityDecision(False, EligibilityReason.INSUFFICIENT_SURVIVOR_EVIDENCE)

        return EligibilityDecision(True, EligibilityReason.ELIGIBLE)


class TargetServiceQueue:
    """Deterministic first-confirmed queue with target-ID deduplication."""

    def __init__(self):
        self._items: list[tuple[datetime, int, FusedTargetEstimate]] = []
        self._queued: set[str] = set()
        self._serviced: set[str] = set()
        self._counter = itertools.count()

    def add(self, target: FusedTargetEstimate) -> bool:
        if target.target_id in self._queued or target.target_id in self._serviced:
            return False
        heapq.heappush(self._items, (target.first_seen_at, next(self._counter), target))
        self._queued.add(target.target_id)
        return True

    def pop(self) -> FusedTargetEstimate | None:
        if not self._items:
            return None
        _, _, target = heapq.heappop(self._items)
        self._queued.discard(target.target_id)
        return target

    def mark_serviced(self, target_id: str) -> None:
        self._serviced.add(target_id)
        self._queued.discard(target_id)

    def is_serviced(self, target_id: str) -> bool:
        return target_id in self._serviced

    def __len__(self) -> int:
        return len(self._items)
