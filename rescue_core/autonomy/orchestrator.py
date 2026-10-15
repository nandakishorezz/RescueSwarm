"""Authoritative Stage 7S mission coordinator independent of transport details."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rescue_core.geolocation.models import FusedTargetEstimate

from .models import SearchResumeCheckpoint
from .policies import EligibilityDecision, TargetEligibilityPolicy, TargetServiceQueue
from .states import RescueEvent, RescueState, RescueStateMachine


@dataclass(frozen=True)
class RescueTransitionRecord:
    prior: RescueState
    event: RescueEvent
    current: RescueState
    metadata: dict[str, Any]


class RescueMissionOrchestrator:
    """Owns state, selected target, queue, and checkpoint invariants.

    MAVLink actions live in the SITL runner. This class cannot open a connection,
    making the domain state machine testable and impossible to point at hardware.
    """

    def __init__(
        self, eligibility: TargetEligibilityPolicy, queue: TargetServiceQueue,
        transition_callback: Callable[[RescueTransitionRecord], None] | None = None,
    ):
        self.machine = RescueStateMachine()
        self.eligibility = eligibility
        self.queue = queue
        self.transition_callback = transition_callback or (lambda _: None)
        self.selected_target: FusedTargetEstimate | None = None
        self.checkpoint: SearchResumeCheckpoint | None = None

    @property
    def state(self) -> RescueState:
        return self.machine.state

    def transition(self, event: RescueEvent, **metadata: Any) -> RescueState:
        prior = self.state
        current = self.machine.transition(event)
        self.transition_callback(RescueTransitionRecord(prior, event, current, metadata))
        return current

    def consider_target(self, target: FusedTargetEstimate, inventory: Any, **context: Any) -> EligibilityDecision:
        decision = self.eligibility.evaluate(
            target, inventory, already_serviced=self.queue.is_serviced(target.target_id), **context
        )
        if decision.eligible:
            self.queue.add(target)
        return decision

    def select_next_target(self) -> FusedTargetEstimate | None:
        if self.selected_target is not None:
            return self.selected_target
        self.selected_target = self.queue.pop()
        return self.selected_target

    def save_checkpoint(self, checkpoint: SearchResumeCheckpoint) -> None:
        if self.state is not RescueState.TARGET_CONFIRMED:
            raise RuntimeError("Search checkpoint requires TARGET_CONFIRMED state")
        self.checkpoint = checkpoint
        self.transition(RescueEvent.CHECKPOINT_SAVED, checkpoint=checkpoint.model_dump(mode="json"))

    def service_finished(self, delivered: bool) -> None:
        if self.selected_target and delivered:
            self.selected_target.delivery_state = "DELIVERED"
            self.queue.mark_serviced(self.selected_target.target_id)
        self.selected_target = None
