"""Payload abstraction with a simulation-only Stage 7S implementation."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from .models import DeliveryEvidence, PayloadSlot, PayloadState


class PayloadError(RuntimeError):
    pass


class PayloadInventory:
    def __init__(self, slots: int = 1):
        if slots < 0:
            raise ValueError("Payload slot count cannot be negative")
        self.slots = [PayloadSlot(slot_id=f"slot_{index + 1}") for index in range(slots)]

    @property
    def remaining(self) -> int:
        return sum(slot.state is PayloadState.AVAILABLE for slot in self.slots)

    def reserve(self, target_id: str) -> PayloadSlot:
        if any(slot.target_id == target_id for slot in self.slots):
            raise PayloadError("Target already owns or consumed a payload slot")
        try:
            slot = next(item for item in self.slots if item.state is PayloadState.AVAILABLE)
        except StopIteration as exc:
            raise PayloadError("No payload is available") from exc
        slot.state, slot.target_id = PayloadState.RESERVED, target_id
        return slot


class PayloadInterface(ABC):
    @abstractmethod
    def release(self, slot: PayloadSlot, **evidence: float) -> DeliveryEvidence: ...

    @abstractmethod
    def verify(self, slot: PayloadSlot, delivery: DeliveryEvidence) -> bool: ...


class SimulatedPayloadAdapter(PayloadInterface):
    """Pure logical adapter with no actuator or flight-controller dependency."""

    def __init__(self, release_success: bool = True, verification_success: bool = True, latency_s: float = 0):
        self.release_success = release_success
        self.verification_success = verification_success
        self.latency_s = max(0, latency_s)

    def release(self, slot: PayloadSlot, **evidence: float) -> DeliveryEvidence:
        if slot.state is not PayloadState.RESERVED or not slot.target_id:
            raise PayloadError("Payload must be reserved before release")
        slot.state = PayloadState.RELEASING
        requested = datetime.now(UTC)
        if self.latency_s:
            time.sleep(self.latency_s)
        slot.state = PayloadState.RELEASED if self.release_success else PayloadState.FAILED
        return DeliveryEvidence(
            target_id=slot.target_id, payload_slot=slot.slot_id,
            release_requested_at=requested,
            release_completed_at=datetime.now(UTC),
            vehicle_east_m=evidence["vehicle_east_m"], vehicle_north_m=evidence["vehicle_north_m"],
            relative_target_error_m=evidence["relative_target_error_m"],
            altitude_m=evidence["altitude_m"], ground_speed_m_s=evidence["ground_speed_m_s"],
            target_uncertainty_m=evidence["target_uncertainty_m"],
            release_succeeded=self.release_success,
        )

    def verify(self, slot: PayloadSlot, delivery: DeliveryEvidence) -> bool:
        if slot.state is not PayloadState.RELEASED or not delivery.release_succeeded:
            slot.state = PayloadState.FAILED
            return False
        delivery.verification_at = datetime.now(UTC)
        delivery.verification_succeeded = self.verification_success
        slot.state = PayloadState.VERIFIED if self.verification_success else PayloadState.UNKNOWN
        return self.verification_success
