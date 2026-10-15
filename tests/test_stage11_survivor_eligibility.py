from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import Polygon

from rescue_core.autonomy.models import SyntheticSurvivorScenario
from rescue_core.autonomy.payload import PayloadInventory
from rescue_core.autonomy.policies import EligibilityReason, TargetEligibilityPolicy, SurvivorEligibilityState
from rescue_core.geolocation.models import FusedTargetEstimate, GeolocationQuality, TargetTrackState


def _target(**changes) -> FusedTargetEstimate:
    now = datetime.now(UTC)
    values = dict(
        target_id="target_01", mission_id="mission", state=TargetTrackState.CONFIRMED,
        east_m=5, north_m=5, latitude=17, longitude=78, uncertainty_m=2.0,
        detection_confidence=0.8, observation_count=5, inlier_count=5, outlier_count=0,
        first_seen_at=now, last_seen_at=now + timedelta(seconds=2),
        quality=GeolocationQuality.STANDARD_GNSS_LIMITED, simulated=True,
    )
    values.update(changes)
    return FusedTargetEstimate(**values)


def _policy() -> TargetEligibilityPolicy:
    return TargetEligibilityPolicy(Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]), 8.0, 25.0, 60.0)


def test_single_frame_detection_not_serviceable():
    # 1. Single-frame person detection -> not immediately serviceable.
    t = _target(observation_count=1, inlier_count=1, state=TargetTrackState.CANDIDATE)
    decision = _policy().evaluate(t, PayloadInventory(1))
    assert not decision.eligible
    assert decision.reason == EligibilityReason.NOT_CONFIRMED

    # Even if forcefully set to confirmed, the survivor layer should reject it
    t2 = _target(observation_count=1, inlier_count=1, state=TargetTrackState.CONFIRMED)
    decision2 = _policy().evaluate(t2, PayloadInventory(1))
    assert not decision2.eligible
    assert decision2.reason == EligibilityReason.INSUFFICIENT_SURVIVOR_EVIDENCE


def test_persistent_person_detection_serviceable():
    # 2. Persistent person detection -> eligibility evidence accumulates.
    # 4. Stable target -> service candidate.
    t = _target(observation_count=5, inlier_count=5)
    decision = _policy().evaluate(t, PayloadInventory(1))
    assert decision.eligible
    assert decision.reason == EligibilityReason.ELIGIBLE


def test_inconsistent_noisy_detection_rejected():
    # 3. Inconsistent/noisy detection -> rejected or remains insufficient.
    # High uncertainty (moving/noisy)
    t1 = _target(uncertainty_m=9.0)
    decision1 = _policy().evaluate(t1, PayloadInventory(1))
    assert not decision1.eligible
    assert decision1.reason == EligibilityReason.UNCERTAINTY_TOO_HIGH
    
    # Low confidence
    t2 = _target(detection_confidence=0.5)
    decision2 = _policy().evaluate(t2, PayloadInventory(1))
    assert not decision2.eligible
    assert decision2.reason == EligibilityReason.INSUFFICIENT_SURVIVOR_EVIDENCE


def test_ineligible_detection_does_not_consume_payload():
    # 6. Ineligible detection does not consume payload.
    inventory = PayloadInventory(2)
    t = _target(detection_confidence=0.4) # Unreliable
    decision = _policy().evaluate(t, inventory)
    assert not decision.eligible
    assert inventory.remaining == 2


def test_ground_truth_firewall():
    # 8. Ground-truth firewall.
    # The policy evaluates strictly FusedTargetEstimate which only contains vision-derived metrics.
    # It does not accept GroundTruthTarget or any explicit SIMULATION_ONLY ground truth flags.
    t = _target()
    assert not hasattr(t, "true_latitude")
    assert not hasattr(t, "true_longitude")
    decision = _policy().evaluate(t, PayloadInventory(1))
    assert decision.eligible


def test_survivor_eligibility_classifier_logic():
    # Tests the inner conservative state machine directly
    classifier = _policy().survivor_classifier
    
    # Stable candidate
    assert classifier.evaluate(_target()) == SurvivorEligibilityState.SERVICE_CANDIDATE
    
    # Insufficient temporal persistence
    assert classifier.evaluate(_target(inlier_count=3)) == SurvivorEligibilityState.INSUFFICIENT_EVIDENCE
    
    # Insufficient vision confidence
    assert classifier.evaluate(_target(detection_confidence=0.6)) == SurvivorEligibilityState.INSUFFICIENT_EVIDENCE
    
    # High uncertainty = not eligible
    assert classifier.evaluate(_target(uncertainty_m=9.0)) == SurvivorEligibilityState.NOT_ELIGIBLE
