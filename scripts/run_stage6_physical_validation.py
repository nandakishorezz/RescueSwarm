#!/usr/bin/env python3
"""Orchestrate opt-in, read-only Stage 6 physical bench procedures.

REMOVE PROPELLERS before every Pixhawk-connected mode. This program contains no
flight-command implementation and wraps the Stage 1 read-only adapter only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.common.diagnostics import write_json
from rescue_core.hardware.live import PhysicalTelemetrySession
from rescue_core.hardware.baseline import collect_power_health
from rescue_core.hardware.metrics import frame_pose_sync_statistics
from rescue_core.hardware.report import render_stage6_report
from rescue_core.hardware.safety import PhysicalBenchSafetyGuard
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.factory import create_camera_source, create_detector
from rescue_core.vision.agent import VisionAgent
from rescue_core.vision.evidence import EvidenceRecorder
from rescue_core.vision.models import detection_result_from_dict
from rescue_core.geolocation.calibration import CalibrationRegistry
from rescue_core.geolocation.config import load_geolocation_config
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import RejectedObservation, TargetObservation, TargetPixelStrategy
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.geolocation.worker import GeolocationWorker
from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from tools.camera_probe import collect_camera_info
from tools.list_camera_modes import list_modes


def _run_directory(root: Path) -> Path:
    path = root / f"{datetime.now().astimezone():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


def _require_pi() -> None:
    model = Path("/proc/device-tree/model")
    if not model.is_file() or "Raspberry Pi" not in model.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("PHYSICAL VALIDATION REQUIRED: this command must run on the Raspberry Pi 5")


def _require_connection(args: argparse.Namespace) -> None:
    if not args.connection:
        raise RuntimeError("Provide the verified --connection and --baud from find_pixhawk_connection.py")
    PhysicalBenchSafetyGuard().require_propellers_removed(args.propellers_removed)


def _require_stable_power(output: Path) -> dict:
    power = collect_power_health()
    write_json(output / "power.json", power)
    if not power.get("valid_for_precision_measurement"):
        raise RuntimeError("INVALID_POWER_CONDITION: resolve current Pi power/thermal flags before precision measurement")
    return power


def _telemetry(args: argparse.Namespace, output: Path, duration: float, timesync: bool = True) -> dict:
    _require_connection(args)
    session = PhysicalTelemetrySession(args.connection, args.baud, args.heartbeat_timeout)
    try:
        identity = session.start()
        summary = session.collect(duration, args.timesync_interval if timesync else None)
        summary["identity"] = identity
        session.write(output)
        write_json(output / "physical_telemetry_summary.json", summary)
        return summary
    finally:
        session.stop()


def _capture_sync(args: argparse.Namespace, output: Path) -> dict:
    _require_connection(args)
    _require_stable_power(output)
    config = load_vision_config(args.vision_config)
    config.camera.source = "picamera2"
    if args.backend:
        config.detector.backend = args.backend
    if args.model:
        config.detector.model_id = args.model
    if config.detector.backend == "mock":
        raise RuntimeError("Physical integration refuses MockDetectorBackend; provide a local ONNX or NCNN model")
    source = create_camera_source(config)
    session = PhysicalTelemetrySession(args.connection, args.baud, args.heartbeat_timeout)
    records: list[dict] = []
    try:
        session.start()
        source.start()
        deadline = time.monotonic() + args.duration
        next_sync = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_sync and session.pose:
                session.pose.request_timesync()
                next_sync = now + args.timesync_interval
            frame = source.read()
            if frame is None:
                continue
            row = {"frame_id": frame.frame_id, "capture_timestamp_ns": frame.capture_timestamp_monotonic_ns}
            try:
                lookup = session.pose.history.lookup(frame.capture_timestamp_monotonic_ns, session.pose.clock_sync) if session.pose else None
                if lookup:
                    row.update({
                        "pose_before_ns": lookup.diagnostics.pose_before_host_ns,
                        "pose_after_ns": lookup.diagnostics.pose_after_host_ns,
                        "interpolation_fraction": lookup.diagnostics.interpolation_fraction,
                        "nearest_sample_age_ms": lookup.diagnostics.nearest_sample_age_ms,
                        "clock_sync_quality": lookup.diagnostics.clock_sync_quality,
                    })
            except ValueError as exc:
                row["rejection_reason"] = str(exc)
            records.append(row)
    finally:
        source.stop()
        session.write(output)
        session.stop()
    (output / "frame_pose_sync.ndjson").write_text(
        "".join(json.dumps(row, default=str, sort_keys=True) + "\n" for row in records), encoding="utf-8"
    )
    metrics = frame_pose_sync_statistics(records)
    write_json(output / "frame_pose_sync_metrics.json", metrics)
    return metrics


def _camera_evidence(output: Path) -> dict:
    result, dmesg = collect_camera_info()
    write_json(output / "camera_probe.json", result)
    (output / "camera_dmesg.txt").write_text(dmesg, encoding="utf-8")
    modes = list_modes(0)
    write_json(output / "camera_modes.json", modes)
    return {"probe": result, "modes": modes}


def _integration_endurance(args: argparse.Namespace, output: Path) -> dict:
    """Run camera, detector, one MAVLink reader, TIMESYNC, pose and geolocation together."""
    _require_connection(args)
    _require_stable_power(output)
    required = (args.calibration_id, args.sensor_mode, args.origin_lat, args.origin_lon)
    if any(value is None for value in required):
        raise RuntimeError(
            "--integration-endurance requires --calibration-id, --sensor-mode, --origin-lat and --origin-lon"
        )
    config = load_vision_config(args.vision_config)
    config.camera.source = "picamera2"
    session = PhysicalTelemetrySession(args.connection, args.baud, args.heartbeat_timeout)
    observations: list[TargetObservation] = []
    rejections: list[RejectedObservation] = []
    vision_events: list[dict] = []
    try:
        session.start()
        assert session.pose is not None
        geo_config = load_geolocation_config(ROOT / "config" / "geolocation.yaml").geolocation
        calibration = CalibrationRegistry(ROOT / "calibration" / "cameras").load_intrinsics(args.calibration_id)
        extrinsics = CalibrationRegistry(ROOT / "calibration" / "cameras").load_extrinsics(args.calibration_id)
        coordinate_frame = LocalCoordinateFrame(GeoPoint(
            latitude=args.origin_lat, longitude=args.origin_lon, altitude_m=args.origin_alt,
        ))
        uncertainty = GeolocationUncertaintyEstimator(
            geo_config.uncertainty.horizontal_position_prior_m,
            geo_config.uncertainty.vertical_position_prior_m,
            geo_config.uncertainty.pixel_prior_px, geo_config.uncertainty.attitude_prior_deg,
            geo_config.uncertainty.mount_prior_deg,
            geo_config.uncertainty.minimum_reported_uncertainty_m,
        )
        geolocator = TargetGeolocator(
            calibration=calibration, extrinsics=extrinsics,
            pose_history=session.pose.history, clock_sync=session.pose.clock_sync,
            ground_model=FlatGroundModel(args.ground_up, "PHYSICALLY_DEFINED_FLAT_PLANE"),
            wgs84_converter=coordinate_frame.enu_to_wgs84, uncertainty=uncertainty,
            pixel_strategy=TargetPixelStrategy(args.pixel_strategy),
            minimum_gps_fix=geo_config.quality.minimum_gps_fix,
            maximum_view_angle_from_nadir_deg=geo_config.quality.maximum_view_angle_from_nadir_deg,
            require_sync=True, require_calibrated_extrinsics=False,
        )
        def locate(payload: dict) -> None:
            result = detection_result_from_dict(payload)
            for detection in result.detections:
                outcome = geolocator.geolocate(
                    result, detection, "drone_01", active_width=calibration.image_width,
                    active_height=calibration.image_height, active_sensor_mode=args.sensor_mode,
                    evidence_references=[result.frame_id],
                )
                (observations if isinstance(outcome, TargetObservation) else rejections).append(outcome)
        worker = GeolocationWorker(locate, queue_size=geo_config.worker.queue_size)
        worker.start()
        events_path = output / "vision_events.ndjson"
        def on_event(event) -> None:
            value = event.as_dict()
            vision_events.append(value)
            if event.type == "vision.detection":
                worker.submit(event.payload)
        evidence = EvidenceRecorder(
            output, True, config.evidence.confidence_threshold, config.evidence.cooldown_s,
            config.evidence.sample_interval_s, config.evidence.max_images,
        )
        agent = VisionAgent(
            create_camera_source(config), create_detector(config, args.backend, args.model), config.queue.size,
            config.camera.no_frame_timeout_s, evidence, on_event,
            output / "vision_status.json", config.camera.restart_attempts,
            config.camera.restart_backoff_s,
        )
        agent_errors: list[str] = []
        def run_agent() -> None:
            try:
                agent.run()
            except Exception as exc:
                agent_errors.append(f"{type(exc).__name__}: {exc}")
        thread = threading.Thread(target=run_agent, name="stage6-vision-agent", daemon=True)
        thread.start()
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and thread.is_alive():
            session.pose.request_timesync()
            time.sleep(min(args.timesync_interval, max(0, deadline - time.monotonic())))
        agent.stop()
        thread.join(10)
        worker.stop(5)
        events_path.write_text(
            "".join(json.dumps(event, default=str, sort_keys=True) + "\n" for event in vision_events),
            encoding="utf-8",
        )
        (output / "observations.ndjson").write_text(
            "".join(item.model_dump_json() + "\n" for item in observations), encoding="utf-8",
        )
        (output / "rejections.ndjson").write_text(
            "".join(item.model_dump_json() + "\n" for item in rejections), encoding="utf-8",
        )
        telemetry = session.summary()
        session.write(output)
        vision_status = agent.status()
        result = {
            "status": "PASS" if not agent_errors and not worker.errors else "FAILED",
            "duration_s": args.duration,
            "vision": vision_status,
            "telemetry": telemetry,
            "geolocation": {
                "observations": len(observations), "rejections": len(rejections),
                "queue_drops": worker.dropped, "errors": worker.errors,
                "calibration_id": calibration.calibration_id, "extrinsics_id": extrinsics.extrinsics_id,
            },
            "agent_errors": agent_errors,
            "flight_commands_sent": False,
        }
        write_json(output / "integration_endurance.json", result)
        return result
    finally:
        session.stop()


def _run_calibration_capture(args: argparse.Namespace, output: Path) -> None:
    if not all((args.width, args.height, args.sensor_mode, args.calibration_id)):
        raise RuntimeError("--calibration requires --width, --height, --sensor-mode and --calibration-id")
    dataset = output / "calibration_dataset"
    command = [
        sys.executable, str(ROOT / "tools" / "capture_calibration_dataset.py"), str(dataset),
        "--width", str(args.width), "--height", str(args.height), "--count", str(args.calibration_images),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise RuntimeError("Calibration capture failed; no calibration was generated")
    print("Capture complete. Run both calibrations with the physically measured --square-size-mm value; selection requires held-out validation.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("baseline", "camera", "mavlink", "gps-stability", "timesync", "capture-sync", "calibration", "geo-local", "geo-full", "all-safe", "integration-endurance"):
        modes.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--connection")
    parser.add_argument("--baud", type=int, choices=(57600, 115200, 921600), default=57600)
    parser.add_argument("--heartbeat-timeout", type=float, default=10)
    parser.add_argument("--propellers-removed", action="store_true")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--timesync-interval", type=float, default=1)
    parser.add_argument("--vision-config", type=Path, default=ROOT / "config" / "vision.yaml")
    parser.add_argument("--backend", choices=("ncnn", "onnx"))
    parser.add_argument("--model")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "hardware_runs")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--sensor-mode")
    parser.add_argument("--calibration-id")
    parser.add_argument("--origin-lat", type=float)
    parser.add_argument("--origin-lon", type=float)
    parser.add_argument("--origin-alt", type=float, default=0)
    parser.add_argument("--ground-up", type=float, default=0)
    parser.add_argument("--pixel-strategy", choices=("BBOX_CENTER", "BBOX_BOTTOM_CENTER"), default="BBOX_BOTTOM_CENTER")
    parser.add_argument("--calibration-images", type=int, default=30)
    parser.add_argument("--estimates", type=Path)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--geo-test", choices=("A", "B"), default="A", help="Local physical test level")
    args = parser.parse_args()
    output = _run_directory(args.output_root)
    try:
        _require_pi()
        if args.baseline or args.all_safe:
            # The baseline owns its exact run directory, so remove the empty orchestrator
            # directory and recreate it through the immutable collector.
            output.rmdir()
            command = [sys.executable, str(ROOT / "scripts" / "run_stage6_hardware_baseline.py"), "--output-dir", str(output)]
            if args.connection:
                _require_connection(args)
                command += ["--connection", args.connection, "--baud", str(args.baud), "--propellers-removed"]
            completed = subprocess.run(command, cwd=ROOT, check=False)
            if completed.returncode:
                return completed.returncode
        if args.camera or args.all_safe:
            _camera_evidence(output)
        if args.all_safe and args.connection:
            _telemetry(args, output, args.duration)
            _capture_sync(args, output)
        if args.mavlink:
            _telemetry(args, output, args.duration, timesync=False)
        if args.gps_stability:
            _require_stable_power(output)
            summary = _telemetry(args, output, args.duration)
            write_json(output / "gps_stability.json", summary["gps_stationary"])
        if args.timesync:
            _require_stable_power(output)
            summary = _telemetry(args, output, args.duration)
            write_json(output / "timesync.json", summary["timesync"])
        if args.capture_sync:
            _capture_sync(args, output)
        if args.calibration:
            _run_calibration_capture(args, output)
        if args.geo_local or args.geo_full:
            if not args.estimates or not args.ground_truth:
                raise RuntimeError("Physical geolocation evaluation requires --estimates and --ground-truth")
            test = args.geo_test if args.geo_local else "C"
            command = [sys.executable, str(ROOT / "tools" / "evaluate_stage6_physical_geolocation.py"), str(args.estimates), str(args.ground_truth), "--test", test, "--output", str(output / f"geolocation_test_{test.lower()}.json")]
            if subprocess.run(command, cwd=ROOT, check=False).returncode:
                return 2
        if args.integration_endurance:
            _integration_endurance(args, output)
        report = render_stage6_report(output)
        (output / "stage6_report.md").write_text(report, encoding="utf-8")
        print(output)
        return 0
    except Exception as exc:
        write_json(output / "failure.json", {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}", "flight_commands_sent": False})
        print(f"FAILED: {exc}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
