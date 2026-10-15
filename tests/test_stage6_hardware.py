from __future__ import annotations

from pathlib import Path
import json

import pytest

from rescue_core.hardware.metrics import (
    attitude_static_statistics,
    frame_pose_sync_statistics,
    gps_stationary_statistics,
    physical_geolocation_errors,
    stream_rate_statistics,
    uncertainty_calibration_verdict,
)
from rescue_core.hardware.models import GroundTruthMethod, GroundTruthTarget
from rescue_core.hardware.power import parse_throttled_output
from rescue_core.hardware.profiles import (
    LocalAutopilotProfile,
    LocalHardwareProfile,
    load_local_hardware_profile,
    write_local_hardware_profile,
    write_local_vision_profile,
)
from rescue_core.hardware.report import render_stage6_report
from rescue_core.hardware.safety import PhysicalBenchSafetyError, PhysicalBenchSafetyGuard
from ground.backend.app.sitl.router import MAVLinkMessageRouter
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.models import Detection, DetectionResult, detection_result_from_dict


def test_physical_bench_guard_requires_propeller_confirmation() -> None:
    guard = PhysicalBenchSafetyGuard()
    with pytest.raises(PhysicalBenchSafetyError, match="REMOVE PROPELLERS"):
        guard.require_propellers_removed(False)
    guard.require_propellers_removed(True)


@pytest.mark.parametrize(
    "operation",
    ["arm", "takeoff", "set_mode", "upload_mission", "start_mission", "goto", "rtl", "land", "servo", "motor_test", "release_payload"],
)
def test_physical_bench_guard_blocks_every_command(operation: str) -> None:
    with pytest.raises(PhysicalBenchSafetyError, match="read-only"):
        getattr(PhysicalBenchSafetyGuard(), operation)()


def test_power_health_parses_current_and_historical_flags() -> None:
    healthy = parse_throttled_output("throttled=0x0")
    assert healthy.valid_for_precision_measurement is True
    assert healthy.status == "VERIFIED"
    unstable = parse_throttled_output("throttled=0x50005")
    assert unstable.undervoltage_now is True
    assert unstable.throttled_now is True
    assert unstable.undervoltage_occurred is True
    assert unstable.throttling_occurred is True
    assert unstable.valid_for_precision_measurement is False
    assert parse_throttled_output("command unavailable").status == "UNKNOWN"


def test_gps_statistics_are_repeatability_not_accuracy() -> None:
    result = gps_stationary_statistics([
        {"latitude_deg": 17.0, "longitude_deg": 78.0},
        {"latitude_deg": 17.000001, "longitude_deg": 78.0},
        {"latitude_deg": 17.0, "longitude_deg": 78.000001},
    ])
    assert result["metric"] == "stationary_repeatability_not_absolute_accuracy"
    assert result["absolute_accuracy"] == "NOT_MEASURED"
    assert result["radial_deviation_m"]["p95"] > 0


def test_stream_attitude_and_frame_pose_statistics() -> None:
    assert stream_rate_statistics([0, 100_000_000, 200_000_000])["observed_hz"] == pytest.approx(10)
    attitude = attitude_static_statistics([
        {"roll_deg": 1.0, "pitch_deg": 2.0, "yaw_deg": 3.0},
        {"roll_deg": 1.2, "pitch_deg": 1.8, "yaw_deg": 3.1},
    ])
    assert attitude["roll_deg"]["std"] > 0
    sync = frame_pose_sync_statistics([
        {"pose_before_ns": 0, "pose_after_ns": 20_000_000},
        {"pose_before_ns": 20_000_000, "pose_after_ns": 40_000_000},
        {"rejection_reason": "POSE_UNAVAILABLE"},
    ])
    assert sync["bracketing_success_percent"] == pytest.approx(200 / 3)
    assert sync["rejected_frames"] == 1


def test_ground_truth_requires_provenance_and_coordinate_pair() -> None:
    truth = GroundTruthTarget(
        target_id="target_1", source=GroundTruthMethod.TAPE_MEASURED_LOCAL,
        expected_accuracy_m=0.02, east_m=3, north_m=2, notes="Steel tape on marked grid",
    )
    assert truth.source == GroundTruthMethod.TAPE_MEASURED_LOCAL
    with pytest.raises(ValueError):
        GroundTruthTarget(target_id="bad", source=GroundTruthMethod.UNKNOWN, east_m=1)


def test_physical_geolocation_errors_and_uncertainty_coverage() -> None:
    truth = GroundTruthTarget(
        target_id="target_1", source=GroundTruthMethod.TAPE_MEASURED_LOCAL,
        expected_accuracy_m=0.02, east_m=3, north_m=2,
    )
    result = physical_geolocation_errors([
        {"east_m": 3.3, "north_m": 2.4, "position_uncertainty_m": 1.0},
        {"east_m": 2.7, "north_m": 1.6, "position_uncertainty_m": 0.2},
    ], truth)
    assert result["radial_error_m"]["p50"] == pytest.approx(0.5)
    assert result["uncertainty_coverage_percent"] == 50
    assert result["absolute_accuracy_measured"] is False
    assert uncertainty_calibration_verdict(result)["status"] == "OVERCONFIDENT"


def test_absolute_accuracy_only_for_surveyed_truth() -> None:
    truth = GroundTruthTarget(
        target_id="surveyed", source=GroundTruthMethod.SURVEYED_GNSS,
        expected_accuracy_m=0.05, true_latitude=17.0, true_longitude=78.0,
    )
    result = physical_geolocation_errors([{"latitude": 17.0, "longitude": 78.0}], truth)
    assert result["absolute_accuracy_measured"] is True


def test_local_hardware_and_camera_profiles_are_evidence_linked(tmp_path: Path) -> None:
    path = tmp_path / "local_hardware.yaml"
    profile = LocalHardwareProfile(
        autopilot=LocalAutopilotProfile(connection="/dev/ttyACM0", baud=115200, evidence_run="run_1")
    )
    write_local_hardware_profile(path, profile)
    assert load_local_hardware_profile(path) == profile
    vision = tmp_path / "local_vision.yaml"
    write_local_vision_profile(vision, {
        "camera_index": 0, "sensor_mode": "mode0", "width": 1280, "height": 800,
        "pixel_format": "Y8", "achieved_fps": 55.2, "evidence_run": "run_1",
    })
    assert "UNVERIFIED" not in vision.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        write_local_vision_profile(vision, {"camera_index": 0})
    compatible = tmp_path / "compatible_local_vision.yaml"
    write_local_vision_profile(compatible, {
        "camera_index": 1, "sensor_mode": "mode0", "width": 1280, "height": 800,
        "pixel_format": "Y8", "achieved_fps": 55.2, "evidence_run": "run_1",
    }, Path("config/vision.yaml"))
    loaded = load_vision_config(compatible)
    assert loaded.camera.source == "picamera2"
    assert loaded.camera.camera_index == 1
    assert loaded.camera.profiles["detection"].verification == "EVIDENCE_BACKED_LOCAL"


def test_stage6_report_is_honest_when_no_hardware_data() -> None:
    report = render_stage6_report(None)
    assert "PHYSICAL VALIDATION INCOMPLETE" in report
    assert "Physical accuracy validated: **NO**" in report
    assert "Physical flight: NOT PERFORMED" in report


def test_stage6_report_uses_measured_evidence_without_inventing_accuracy(tmp_path: Path) -> None:
    (tmp_path / "system.json").write_text(json.dumps({"platform": {"raspberry_pi": True, "model": "Raspberry Pi 5 Model B"}}), encoding="utf-8")
    (tmp_path / "power.json").write_text(json.dumps({"status": "VERIFIED", "valid_for_precision_measurement": True}), encoding="utf-8")
    (tmp_path / "camera_probe.json").write_text(json.dumps({"status": "DETECTED"}), encoding="utf-8")
    (tmp_path / "mavlink_probe.json").write_text(json.dumps({"status": "VERIFIED", "heartbeat": "VERIFIED", "connection": "/dev/ttyACM0", "baud": 115200, "firmware": {"firmware_version": "4.x"}}), encoding="utf-8")
    report = render_stage6_report(tmp_path)
    assert "Raspberry Pi 5 | VERIFIED" in report
    assert "OV9281 | DETECTED" in report
    assert "Absolute accuracy: NOT_MEASURED" in report


def test_shared_mavlink_router_uses_bounded_queues() -> None:
    router = MAVLinkMessageRouter(object(), queue_size=2)
    queue = router._queues["GPS_RAW_INT"]
    assert queue.maxsize == 2
    with pytest.raises(ValueError):
        MAVLinkMessageRouter(object(), queue_size=0)


def test_physical_detection_evidence_round_trips_original_pixels() -> None:
    result = DetectionResult(
        frame_id="physical:1", source_id="ov9281", capture_timestamp_monotonic_ns=100,
        capture_timestamp_wall_ns=200, inference_started_ns=110, inference_finished_ns=120,
        model_id="local", backend="onnx", original_channel_format="Y8",
        model_input_format="RGB_FLOAT32_CHW",
        detections=[Detection(0, "person", 0.8, 10, 20, 30, 60, 1280, 800)],
        preprocess_ms=1, inference_ms=2, postprocess_ms=1, end_to_end_ms=4,
    )
    restored = detection_result_from_dict(result.as_dict())
    assert restored.detections[0].bottom_center_x == 20
    assert restored.detections[0].bottom_center_y == 60


@pytest.mark.pi
def test_stage6_pi_hardware_is_opt_in() -> None:
    model = Path("/proc/device-tree/model")
    if not model.is_file():
        pytest.skip("NOT_TESTED: Raspberry Pi hardware is not attached to this host")
    assert "Raspberry Pi" in model.read_text(encoding="utf-8", errors="replace")


@pytest.mark.camera
def test_stage6_camera_hardware_is_opt_in() -> None:
    pytest.skip("NOT_TESTED: run scripts/run_stage6_physical_validation.py --camera intentionally on Pi 5")


@pytest.mark.pixhawk
@pytest.mark.gps
@pytest.mark.physical_geo
def test_stage6_pixhawk_geolocation_is_opt_in() -> None:
    pytest.skip("NOT_TESTED: physical Pixhawk/M8N/geolocation requires the propeller-free bench fixture")
