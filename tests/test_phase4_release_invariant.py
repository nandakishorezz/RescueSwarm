"""Direct regression tests for the production Phase-4 release invariant."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ground.backend.app.sitl.phase4_service import delivery_release_invariant


def _gate() -> dict[str, dict[str, object]]:
    names = (
        "target_visible", "track_fresh_s", "consecutive_observations",
        "vehicle_armed", "flight_mode_guided", "clock_pose_valid",
        "geolocation_valid", "observation_valid", "payload_available",
        "target_owned", "inside_geofence", "delivery_altitude_error_m",
        "horizontal_speed_m_s", "alignment_error_m", "impact_predictor_valid",
        "native_failsafe_inactive", "emergency_recall_inactive",
    )
    return {name: {"value": True, "passed": True} for name in names}


def _allowed(gate, *, age=0.10, dwell=2.01):
    gate = deepcopy(gate)
    gate["track_fresh_s"] = {"value": age, "passed": age <= 0.75}
    return delivery_release_invariant(gate, dwell_elapsed_s=dwell, required_dwell_s=2.0)


def test_r1_fresh_and_complete_dwell_allows_release():
    assert _allowed(_gate(), age=0.74, dwell=2.01)


def test_r2_stale_observation_forbids_release():
    assert not _allowed(_gate(), age=0.76, dwell=2.01)


def test_r3_short_dwell_forbids_release():
    assert not _allowed(_gate(), age=0.10, dwell=1.99)


def test_r4_stale_and_short_dwell_forbids_release():
    assert not _allowed(_gate(), age=0.76, dwell=1.00)


def _failed(name: str) -> dict[str, dict[str, object]]:
    gate = _gate()
    gate[name] = {"value": False, "passed": False}
    return gate


def test_r5_position_failure_forbids_release():
    assert not _allowed(_failed("alignment_error_m"))


def test_r6_speed_failure_forbids_release():
    assert not _allowed(_failed("horizontal_speed_m_s"))


def test_r7_altitude_failure_forbids_release():
    assert not _allowed(_failed("delivery_altitude_error_m"))


def test_r8_identity_failure_forbids_release():
    assert not _allowed(_failed("target_owned"))


def test_r9_predictor_failure_forbids_release():
    assert not _allowed(_failed("impact_predictor_valid"))


def test_r10_continuous_two_second_gate_has_one_release_call_site():
    source = Path("ground/backend/app/sitl/phase4_service.py").read_text(encoding="utf-8")
    assert source.count("self.payload.release(") == 1
    assert "delivery_release_invariant(" in source
    assert not _allowed(_gate(), dwell=1.999)
    assert _allowed(_gate(), dwell=2.000)

