#!/usr/bin/env python3
"""Replay real bench detections and physical Pixhawk poses through Stage 5 geolocation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from rescue_core.common.diagnostics import write_json
from rescue_core.geolocation.calibration import CalibrationRegistry
from rescue_core.geolocation.clock_sync import ClockSyncEstimator
from rescue_core.geolocation.config import load_geolocation_config
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import RejectedObservation, TargetObservation, TargetPixelStrategy
from rescue_core.geolocation.pose import PoseHistory, PoseSample
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.hardware.metrics import physical_geolocation_errors, uncertainty_calibration_verdict
from rescue_core.hardware.models import GroundTruthTarget
from rescue_core.vision.models import DetectionResult, detection_result_from_dict


def _load_sync(path: Path | None, config) -> ClockSyncEstimator:
    estimator = ClockSyncEstimator(
        config.minimum_samples, config.maximum_samples, config.max_rtt_ms, config.degraded_rtt_p95_ms,
    )
    if path and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("message_type") != "TIMESYNC":
                continue
            fields = row.get("fields", {})
            tc1, ts1 = int(fields.get("tc1", 0)), int(fields.get("ts1", 0))
            if tc1 > 0 and ts1 > 0:
                estimator.observe_response(ts1, tc1, int(row["received_monotonic_ns"]))
    return estimator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--poses", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--registry", type=Path, default=ROOT / "calibration" / "cameras")
    parser.add_argument("--sensor-mode", required=True)
    parser.add_argument("--origin-lat", type=float, required=True)
    parser.add_argument("--origin-lon", type=float, required=True)
    parser.add_argument("--origin-alt", type=float, default=0)
    parser.add_argument("--ground-up-m", type=float, default=0)
    parser.add_argument("--pixel-strategy", choices=("BBOX_CENTER", "BBOX_BOTTOM_CENTER"), default="BBOX_BOTTOM_CENTER")
    parser.add_argument("--test", choices=("A", "B", "C"), required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--power-evidence", type=Path, required=True)
    parser.add_argument("--allow-unsynced", action="store_true", help="Permitted only for isolated Test A")
    args = parser.parse_args()
    if args.allow_unsynced and args.test != "A":
        parser.error("Only geometry-isolation Test A may use --allow-unsynced")
    power = json.loads(args.power_evidence.read_text(encoding="utf-8"))
    if not power.get("valid_for_precision_measurement"):
        raise RuntimeError("INVALID_POWER_CONDITION: physical geolocation evidence is not valid")
    settings = load_geolocation_config(ROOT / "config" / "geolocation.yaml").geolocation
    registry = CalibrationRegistry(args.registry)
    calibration = registry.load_intrinsics(args.calibration_id)
    extrinsics = registry.load_extrinsics(args.calibration_id)
    pose_samples = [
        PoseSample.model_validate_json(line)
        for line in args.poses.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    replay_span_s = (
        (pose_samples[-1].autopilot_boot_time_ns - pose_samples[0].autopilot_boot_time_ns) / 1e9 + 1
        if len(pose_samples) > 1 else settings.pose.history_seconds
    )
    history = PoseHistory(
        max(settings.pose.history_seconds, replay_span_s),
        settings.pose.max_interpolation_gap_ms, settings.pose.max_extrapolation_ms,
    )
    for sample in pose_samples:
        history.add(sample)
    sync = _load_sync(args.telemetry, settings.clock_sync)
    frame = LocalCoordinateFrame(GeoPoint(latitude=args.origin_lat, longitude=args.origin_lon, altitude_m=args.origin_alt))
    uncertainty = GeolocationUncertaintyEstimator(
        settings.uncertainty.horizontal_position_prior_m, settings.uncertainty.vertical_position_prior_m,
        settings.uncertainty.pixel_prior_px, settings.uncertainty.attitude_prior_deg,
        settings.uncertainty.mount_prior_deg, settings.uncertainty.minimum_reported_uncertainty_m,
    )
    geolocator = TargetGeolocator(
        calibration=calibration, extrinsics=extrinsics, pose_history=history,
        clock_sync=sync, ground_model=FlatGroundModel(args.ground_up_m, "PHYSICALLY_DEFINED_FLAT_PLANE"),
        wgs84_converter=frame.enu_to_wgs84, uncertainty=uncertainty,
        pixel_strategy=TargetPixelStrategy(args.pixel_strategy),
        minimum_gps_fix=0 if args.test == "A" else settings.quality.minimum_gps_fix,
        maximum_view_angle_from_nadir_deg=settings.quality.maximum_view_angle_from_nadir_deg,
        require_sync=not args.allow_unsynced, require_calibrated_extrinsics=False,
    )
    observations: list[TargetObservation] = []
    rejections: list[RejectedObservation] = []
    for line in args.detections.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        result = detection_result_from_dict(json.loads(line))
        for detection in result.detections:
            outcome = geolocator.geolocate(
                result, detection, "drone_01", active_width=calibration.image_width,
                active_height=calibration.image_height, active_sensor_mode=args.sensor_mode,
                evidence_references=[result.frame_id],
            )
            (observations if isinstance(outcome, TargetObservation) else rejections).append(outcome)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "observations.ndjson").write_text(
        "".join(item.model_dump_json() + "\n" for item in observations), encoding="utf-8"
    )
    (args.output / "rejections.ndjson").write_text(
        "".join(item.model_dump_json() + "\n" for item in rejections), encoding="utf-8"
    )
    truth = GroundTruthTarget.model_validate_json(args.ground_truth.read_text(encoding="utf-8"))
    if truth.expected_accuracy_m is None or not truth.notes.strip():
        raise ValueError("Physical ground truth requires expected_accuracy_m and non-empty notes")
    estimates = [
        {"east_m": item.target_east_m, "north_m": item.target_north_m, "latitude": item.target_latitude,
         "longitude": item.target_longitude, "position_uncertainty_m": item.position_uncertainty_m}
        for item in observations
    ]
    metrics = physical_geolocation_errors(estimates, truth)
    write_json(args.output / "physical_geolocation.json", {
        "status": metrics["status"], "test": args.test, "calibration_id": calibration.calibration_id,
        "extrinsics_id": extrinsics.extrinsics_id, "pixel_strategy": args.pixel_strategy,
        "clock_sync": sync.diagnostics(), "observations": len(observations), "rejections": len(rejections),
        "metrics": metrics, "uncertainty_calibration": uncertainty_calibration_verdict(metrics),
        "power_evidence": str(args.power_evidence),
        "physical_flight": False, "flight_commands_sent": False,
    })
    print(args.output)
    return 0 if metrics["status"] == "MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
