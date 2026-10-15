from __future__ import annotations

import json

import pytest

from rescue_core.geolocation.evaluation import geolocation_metrics
from rescue_core.geolocation.events import observation_event, target_event
from rescue_core.geolocation.models import TargetTrackState
from tools.generate_calibration_target import generate_checkerboard
from tools.stage5_synthetic_validation import run_synthetic_validation


def test_printable_checkerboard_declares_dimensions_and_measurement_warning(tmp_path) -> None:
    path = tmp_path / "checkerboard.svg"
    generate_checkerboard(9, 6, 25, path)
    content = path.read_text()
    assert 'width="250mm"' in content
    assert "9x6 INNER CORNERS" in content
    assert "NOMINAL 25 mm" in content
    assert "MEASURE AFTER PRINTING" in content


def test_geolocation_metrics_include_percentiles_and_axis_errors() -> None:
    metrics = geolocation_metrics([("a", 3, 4), ("b", 0, 0), ("c", -3, 4)])
    assert metrics["status"] == "MEASURED"
    assert metrics["rmse_m"] == pytest.approx((50 / 3) ** 0.5)
    assert metrics["p95_m"] == pytest.approx(5)
    assert geolocation_metrics([])["status"] == "NOT_MEASURED"


def test_deterministic_synthetic_validation_and_sensitivity_results() -> None:
    first = run_synthetic_validation(seed=7, monte_carlo_samples=25)
    second = run_synthetic_validation(seed=7, monte_carlo_samples=25)
    assert first["metrics"] == second["metrics"]
    metrics = first["metrics"]
    assert metrics["synthetic_zero_noise_error_m"] < 1e-8
    assert metrics["sensitivity"]["timestamp"]["200_ms"] > metrics["sensitivity"]["timestamp"]["100_ms"]
    assert metrics["sensitivity"]["attitude"]["roll_2_deg"] > metrics["sensitivity"]["attitude"]["roll_1_deg"]
    assert metrics["sensitivity"]["extrinsics"]["tilt_2_deg"] > metrics["sensitivity"]["extrinsics"]["tilt_1_deg"]
    assert metrics["sensitivity"]["gnss_common_bias_error_m"] == pytest.approx(2)
    target = first["targets"][0]
    assert target.state is TargetTrackState.CONFIRMED


def test_normalized_target_events_keep_confidence_and_geo_uncertainty_separate() -> None:
    result = run_synthetic_validation(seed=4, monte_carlo_samples=10)
    observation = result["observations"][0]
    target = result["targets"][0]
    obs_event = observation_event("mission", observation)
    fused_event = target_event(target)
    assert obs_event.type == "target.observation"
    assert "camera_ray" not in obs_event.payload  # WebSocket remains compact.
    assert fused_event.payload["detection_confidence"] != fused_event.payload["geolocation_uncertainty_m"]
    assert fused_event.type == "target.confirmed"

