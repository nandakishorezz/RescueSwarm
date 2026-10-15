from __future__ import annotations

from pathlib import Path

from tools.list_camera_modes import list_modes, validate_profile_against_modes
from tools.prepare_ov9281_config import inspect_config, proposed_config
from tools.generate_stage4_report import render


def test_ov9281_configuration_proposal_is_minimal_and_idempotent() -> None:
    original = "# keep\nenable_uart=1\ncamera_auto_detect=1\n"
    proposed, blockers = proposed_config(original, "cam0")
    assert not blockers
    assert "enable_uart=1" in proposed
    assert proposed.count("camera_auto_detect=0") == 1
    assert proposed.count("dtoverlay=ov9281,cam0") == 1
    second, blockers = proposed_config(proposed, "cam0")
    assert second == proposed and not blockers


def test_ov9281_configuration_reports_duplicates_and_conflicts() -> None:
    text = "camera_auto_detect=0\ndtoverlay=ov9281\ndtoverlay=ov9281,cam0\ndtoverlay=imx219\nstart_x=1\n"
    inspection = inspect_config(text)
    assert inspection.duplicate_ov9281
    assert inspection.conflicting_camera_overlays == ["dtoverlay=imx219"]
    assert inspection.legacy_lines == ["start_x=1"]
    _, blockers = proposed_config(text, "auto")
    assert len(blockers) >= 2


def test_camera_mode_discovery_is_honest_on_non_pi() -> None:
    result = list_modes()
    if not result["system"]["raspberry_pi"]:
        assert result["status"] == "NOT_TESTED"
        assert result["cameras"] == []


def test_camera_profile_must_match_discovered_mode_and_rate() -> None:
    modes = {"sensor_modes": [{"size": [1280, 800], "fps": 60.0}]}
    assert validate_profile_against_modes(modes, 1280, 800, 30)[0]
    assert not validate_profile_against_modes(modes, 1280, 800, 90)[0]
    assert not validate_profile_against_modes(modes, 640, 480, 30)[0]


def test_stage4_report_never_fabricates_missing_hardware_or_accuracy() -> None:
    report = render(None)
    assert "CAMERA HARDWARE: NOT_TESTED" in report
    assert "Precision: NOT_MEASURED" in report
    assert "Final survivor accuracy validated: NO" in report
