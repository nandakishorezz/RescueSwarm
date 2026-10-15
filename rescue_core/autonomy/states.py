"""Explicit rescue state-transition graph."""

from __future__ import annotations

from enum import StrEnum


class RescueState(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    TAKEOFF = "TAKEOFF"
    SEARCHING = "SEARCHING"
    TARGET_CANDIDATE = "TARGET_CANDIDATE"
    TARGET_CONFIRMED = "TARGET_CONFIRMED"
    SEARCH_SUSPENDING = "SEARCH_SUSPENDING"
    APPROACH_PLANNING = "APPROACH_PLANNING"
    GUIDED_APPROACH = "GUIDED_APPROACH"
    REACQUIRING = "REACQUIRING"
    PRECISION_ALIGNING = "PRECISION_ALIGNING"
    DROP_READY = "DROP_READY"
    DELIVERING = "DELIVERING"
    DELIVERY_VERIFYING = "DELIVERY_VERIFYING"
    SEARCH_RESUMING = "SEARCH_RESUMING"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    COMPLETED = "COMPLETED"
    ABORTING = "ABORTING"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class RescueEvent(StrEnum):
    PREPARE = "PREPARE"
    MISSION_ARMED = "MISSION_ARMED"
    TAKEOFF_OBSERVED = "TAKEOFF_OBSERVED"
    CANDIDATE_OBSERVED = "CANDIDATE_OBSERVED"
    CANDIDATE_CLEARED = "CANDIDATE_CLEARED"
    TARGET_CONFIRMED = "TARGET_CONFIRMED"
    TARGET_DEFERRED = "TARGET_DEFERRED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    APPROACH_PATH_READY = "APPROACH_PATH_READY"
    GUIDED_CONFIRMED = "GUIDED_CONFIRMED"
    APPROACH_REACHED = "APPROACH_REACHED"
    TARGET_REACQUIRED = "TARGET_REACQUIRED"
    ALIGNMENT_CONVERGED = "ALIGNMENT_CONVERGED"
    DROP_CONDITIONS_MET = "DROP_CONDITIONS_MET"
    RELEASE_ACCEPTED = "RELEASE_ACCEPTED"
    DELIVERY_VERIFIED = "DELIVERY_VERIFIED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    AUTO_CONFIRMED = "AUTO_CONFIRMED"
    RTL_OBSERVED = "RTL_OBSERVED"
    LANDING_OBSERVED = "LANDING_OBSERVED"
    DISARMED = "DISARMED"
    ABORT = "ABORT"
    ABORTED = "ABORTED"
    FAIL = "FAIL"


class InvalidRescueTransition(RuntimeError):
    pass


_TRANSITIONS: dict[tuple[RescueState, RescueEvent], RescueState] = {
    (RescueState.IDLE, RescueEvent.PREPARE): RescueState.PREPARING,
    (RescueState.PREPARING, RescueEvent.MISSION_ARMED): RescueState.TAKEOFF,
    (RescueState.TAKEOFF, RescueEvent.TAKEOFF_OBSERVED): RescueState.SEARCHING,
    (RescueState.SEARCHING, RescueEvent.CANDIDATE_OBSERVED): RescueState.TARGET_CANDIDATE,
    (RescueState.TARGET_CANDIDATE, RescueEvent.CANDIDATE_CLEARED): RescueState.SEARCHING,
    (RescueState.TARGET_CANDIDATE, RescueEvent.TARGET_CONFIRMED): RescueState.TARGET_CONFIRMED,
    (RescueState.SEARCHING, RescueEvent.TARGET_CONFIRMED): RescueState.TARGET_CONFIRMED,
    (RescueState.TARGET_CONFIRMED, RescueEvent.TARGET_DEFERRED): RescueState.SEARCHING,
    (RescueState.TARGET_CONFIRMED, RescueEvent.CHECKPOINT_SAVED): RescueState.SEARCH_SUSPENDING,
    (RescueState.SEARCH_SUSPENDING, RescueEvent.APPROACH_PATH_READY): RescueState.APPROACH_PLANNING,
    (RescueState.APPROACH_PLANNING, RescueEvent.GUIDED_CONFIRMED): RescueState.GUIDED_APPROACH,
    (RescueState.GUIDED_APPROACH, RescueEvent.APPROACH_REACHED): RescueState.REACQUIRING,
    (RescueState.GUIDED_APPROACH, RescueEvent.DELIVERY_FAILED): RescueState.SEARCH_RESUMING,
    (RescueState.REACQUIRING, RescueEvent.TARGET_REACQUIRED): RescueState.PRECISION_ALIGNING,
    (RescueState.PRECISION_ALIGNING, RescueEvent.ALIGNMENT_CONVERGED): RescueState.DROP_READY,
    (RescueState.DROP_READY, RescueEvent.DROP_CONDITIONS_MET): RescueState.DELIVERING,
    (RescueState.DELIVERING, RescueEvent.RELEASE_ACCEPTED): RescueState.DELIVERY_VERIFYING,
    (RescueState.DELIVERY_VERIFYING, RescueEvent.DELIVERY_VERIFIED): RescueState.SEARCH_RESUMING,
    (RescueState.DELIVERY_VERIFYING, RescueEvent.DELIVERY_FAILED): RescueState.SEARCH_RESUMING,
    (RescueState.DELIVERY_VERIFYING, RescueEvent.APPROACH_PATH_READY): RescueState.APPROACH_PLANNING,
    (RescueState.REACQUIRING, RescueEvent.DELIVERY_FAILED): RescueState.SEARCH_RESUMING,
    (RescueState.PRECISION_ALIGNING, RescueEvent.DELIVERY_FAILED): RescueState.SEARCH_RESUMING,
    (RescueState.DROP_READY, RescueEvent.DELIVERY_FAILED): RescueState.SEARCH_RESUMING,
    (RescueState.SEARCH_RESUMING, RescueEvent.APPROACH_PATH_READY): RescueState.APPROACH_PLANNING,
    (RescueState.SEARCH_RESUMING, RescueEvent.TARGET_CONFIRMED): RescueState.TARGET_CONFIRMED,
    (RescueState.SEARCH_RESUMING, RescueEvent.CHECKPOINT_SAVED): RescueState.SEARCH_SUSPENDING,
    (RescueState.SEARCH_RESUMING, RescueEvent.AUTO_CONFIRMED): RescueState.SEARCHING,
    (RescueState.SEARCHING, RescueEvent.RTL_OBSERVED): RescueState.RETURNING,
    (RescueState.RETURNING, RescueEvent.LANDING_OBSERVED): RescueState.LANDING,
    (RescueState.LANDING, RescueEvent.DISARMED): RescueState.COMPLETED,
}


class RescueStateMachine:
    """Small authoritative graph; guard checks happen before transition calls."""

    def __init__(self, initial: RescueState = RescueState.IDLE):
        self.state = initial
        self.history: list[tuple[RescueState, RescueEvent, RescueState]] = []

    def transition(self, event: RescueEvent) -> RescueState:
        if event is RescueEvent.FAIL and self.state not in {RescueState.COMPLETED, RescueState.ABORTED}:
            next_state = RescueState.FAILED
        elif event is RescueEvent.ABORT and self.state not in {
            RescueState.IDLE, RescueState.COMPLETED, RescueState.ABORTED, RescueState.FAILED
        }:
            next_state = RescueState.ABORTING
        elif event is RescueEvent.ABORTED and self.state is RescueState.ABORTING:
            next_state = RescueState.ABORTED
        else:
            try:
                next_state = _TRANSITIONS[(self.state, event)]
            except KeyError as exc:
                raise InvalidRescueTransition(f"Invalid rescue transition: {self.state} + {event}") from exc
        prior = self.state
        self.state = next_state
        self.history.append((prior, event, next_state))
        return next_state
