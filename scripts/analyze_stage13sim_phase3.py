"""Observer-only Phase-3 truth/measurement/estimate and timing analysis."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.models import RejectedObservation
from rescue_core.geolocation.pose import PoseHistory, PoseSample
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.simulation.perception.pipeline import simulation_camera_geometry
from rescue_core.simulation.profiles import CameraProfile
from rescue_core.vision.models import detection_result_from_dict


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _p(values: list[float], percentile: float) -> float | None:
    return float(np.percentile(values, percentile)) if values else None


def _summary(values: list[float], unit: str) -> dict[str, Any]:
    return {
        "count": len(values), "unit": unit,
        "p50": _p(values, 50), "p95": _p(values, 95),
        "maximum": max(values) if values else None,
    }


def _qmul(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw)


def _qangle(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    denominator = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    dot = abs(sum(x*y for x, y in zip(a, b, strict=True))) / denominator
    return math.degrees(2 * math.acos(min(1.0, dot)))


def _qrotate(q: list[float] | tuple[float, ...], vector: tuple[float, float, float]) -> tuple[float, ...]:
    w, x, y, z = q
    return _qmul(_qmul(q, (0.0, *vector)), (w, -x, -y, -z))[1:]


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a-b+180) % 360-180)


def _change_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    changes: list[float] = []
    last_value: Any = object()
    last_time: float | None = None
    for row in rows:
        value = row.get(key)
        if value is None or value == last_value:
            continue
        now = float(row["simulation_elapsed_s"])
        if last_time is not None and now > last_time:
            changes.append(now - last_time)
        last_value, last_time = value, now
    return 1 / statistics.median(changes) if changes else None


def analyze(run_dir: Path) -> dict[str, Any]:
    estimates = _rows(run_dir / "autopilot_estimate.jsonl")
    truth = _rows(run_dir / "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl")
    georef = json.loads((run_dir / "georeference.json").read_text(encoding="utf-8"))
    profile = json.loads((run_dir / "sensor_profile.json").read_text(encoding="utf-8"))
    origin = georef["origin_wgs84"]
    frame = LocalCoordinateFrame(GeoPoint(latitude=origin[0], longitude=origin[1], altitude_m=origin[2]))
    position_errors: list[float] = []
    horizontal_errors: list[float] = []
    altitude_errors: list[float] = []
    gps_horizontal_errors: list[float] = []
    gps_vertical_errors: list[float] = []
    attitude_errors: list[float] = []
    velocity_errors: list[float] = []
    heading_errors: list[float] = []
    clock_path = run_dir / "DIRECT_SIM_CLOCK_EVIDENCE.json"
    clock = json.loads(clock_path.read_text(encoding="utf-8"))["mapping"] if clock_path.is_file() else None
    def truth_ap_ns(item: dict[str, Any]) -> int:
        gazebo_ns = round(float(item["sim_time_s"]) * 1e9)
        if clock:
            return int(clock["destination_anchor_ns"]) + round(
                float(clock["slope"]) * (gazebo_ns-int(clock["source_anchor_ns"]))
                + float(clock["intercept_at_anchor_ns"])
            )
        return gazebo_ns
    q_ned_to_enu = (0.0, math.sqrt(0.5), math.sqrt(0.5), 0.0)
    for estimate in estimates:
        if not truth:
            break
        target_ap_ns = int(estimate["ap_sim_source_time_ns"])
        actual_index = min(range(len(truth)), key=lambda i: abs(truth_ap_ns(truth[i])-target_ap_ns))
        actual = truth[actual_index]
        actual_xyz = actual["vehicle_position_gazebo_m"]
        if estimate.get("latitude") is not None:
            point = frame.to_enu(GeoPoint(
                latitude=float(estimate["latitude"]), longitude=float(estimate["longitude"]),
                altitude_m=float(origin[2]) + float(estimate["relative_altitude_m"]),
            ))
            delta = (point.east_m-actual_xyz[0], point.north_m-actual_xyz[1], point.up_m-actual_xyz[2])
            horizontal_errors.append(math.hypot(delta[0], delta[1]))
            altitude_errors.append(abs(delta[2]))
            position_errors.append(math.sqrt(sum(value*value for value in delta)))
        gps = estimate.get("gps_measurement")
        if gps:
            point = frame.to_enu(GeoPoint(
                latitude=float(gps["latitude"]), longitude=float(gps["longitude"]),
                altitude_m=float(gps["altitude_msl_m"]),
            ))
            gps_horizontal_errors.append(math.hypot(point.east_m-actual_xyz[0], point.north_m-actual_xyz[1]))
            gps_vertical_errors.append(abs(point.up_m-actual_xyz[2]))
        if estimate.get("quaternion_body_to_ned"):
            estimated_q = _qmul(q_ned_to_enu, estimate["quaternion_body_to_ned"])
            truth_body_q = _qmul(actual["vehicle_quaternion_gazebo"], (0.0, 1.0, 0.0, 0.0))
            attitude_errors.append(_qangle(estimated_q, truth_body_q))
            ekf_forward_ned = _qrotate(estimate["quaternion_body_to_ned"], (1, 0, 0))
            truth_forward_enu = _qrotate(actual["vehicle_quaternion_gazebo"], (1, 0, 0))
            ekf_heading = math.degrees(math.atan2(ekf_forward_ned[1], ekf_forward_ned[0]))
            truth_heading = math.degrees(math.atan2(truth_forward_enu[0], truth_forward_enu[1]))
            heading_errors.append(_angle_delta_deg(ekf_heading, truth_heading))
        if 0 < actual_index < len(truth)-1 and estimate.get("velocity_ned_mps"):
            before, after = truth[actual_index-1], truth[actual_index+1]
            dt = float(after["sim_time_s"]) - float(before["sim_time_s"])
            if dt > 0:
                v_truth = [(after["vehicle_position_gazebo_m"][i]-before["vehicle_position_gazebo_m"][i])/dt for i in range(3)]
                vn, ve, vd = estimate["velocity_ned_mps"]
                velocity_errors.append(math.dist((ve, vn, -vd), v_truth))

    sample_times = [float(row["simulation_elapsed_s"]) for row in estimates]
    estimate_rate = (
        (len(sample_times)-1)/(sample_times[-1]-sample_times[0])
        if len(sample_times) > 1 and sample_times[-1] > sample_times[0] else None
    )
    gps_keys = [
        (row.get("gps_measurement") or {}).get("latitude") for row in estimates
    ]
    imu_keys = [
        tuple((row.get("imu_measurement") or {}).values()) if row.get("imu_measurement") else None
        for row in estimates
    ]
    baro_keys = [
        (row.get("barometer_measurement") or {}).get("absolute_pressure_hpa") for row in estimates
    ]
    rate_rows = [dict(row, gps_key=gps_keys[i], imu_key=imu_keys[i], baro_key=baro_keys[i]) for i, row in enumerate(estimates)]
    imu_rows = [row["imu_measurement"] for row in estimates if row.get("imu_measurement")]
    sensor_metrics = {
        "classification": "SIMULATION_EVALUATOR_ONLY",
        "profile_id": profile["profile_id"],
        "configured_update_rates_hz": {
            "gps": profile["gps"]["update_rate_hz"], "camera": 30.0,
            "mavlink_ekf": 25.0, "imu_request": 20.0, "barometer_request": 10.0,
        },
        "measured_update_rates_hz": {
            "gps": _change_rate(rate_rows, "gps_key"),
            "imu": _change_rate(rate_rows, "imu_key"),
            "barometer": _change_rate(rate_rows, "baro_key"),
            "mavlink_ekf_pose": estimate_rate,
        },
        "gps_measurement_horizontal_error_m": _summary(gps_horizontal_errors, "m"),
        "gps_measurement_vertical_error_m": _summary(gps_vertical_errors, "m"),
        "raw_gyro_vector_millirad_s": {
            axis: _summary([float(row[axis]) for row in imu_rows], "millirad/s")
            for axis in ("xgyro_millirad_s", "ygyro_millirad_s", "zgyro_millirad_s")
        },
        "raw_accelerometer_vector_mg": {
            axis: _summary([float(row[axis]) for row in imu_rows], "mg")
            for axis in ("xacc_mg", "yacc_mg", "zacc_mg")
        },
        "raw_magnetometer_vector_mgauss": {
            axis: _summary([float(row[axis]) for row in imu_rows], "mGauss")
            for axis in ("xmag_mgauss", "ymag_mgauss", "zmag_mgauss")
        },
    }
    estimator = {
        "classification": "SIMULATION_EVALUATOR_ONLY_TRUTH_VS_AUTOPILOT_ESTIMATE",
        "position_error_3d_m": _summary(position_errors, "m"),
        "horizontal_position_error_m": _summary(horizontal_errors, "m"),
        "altitude_error_m": _summary(altitude_errors, "m"),
        "velocity_error_mps": _summary(velocity_errors, "m/s"),
        "attitude_angular_error_deg": _summary(attitude_errors, "deg"),
        "heading_error_deg": _summary(heading_errors, "deg"),
        "ekf_initialization_time_s": min(sample_times) if sample_times else None,
        "ekf_healthy_time_s": next((float(row["simulation_elapsed_s"]) for row in estimates
                                     if (row.get("gps_measurement") or {}).get("fix_type", 0) >= 3), None),
        "truth_used_by_autonomy": False,
    }
    timing_control = _latest_pose_control(run_dir, estimates, frame)
    geolocation = json.loads((run_dir / "geolocation_metrics.json").read_text(encoding="utf-8"))
    targets = geolocation.get("unique_final_target_errors", [])
    uncertainty = {
        "classification": "SIMULATION_EVALUATOR_ONLY",
        "profile_id": profile["profile_id"],
        "targets": targets,
        "contained": sum(bool(item.get("truth_inside_uncertainty")) for item in targets),
        "total": len(targets),
        "statistical_calibration_claimed": False,
        "confidence_convention": "REFERENCE_PROFILE GPS accuracy multiplied by 1.645 for a 95% engineering bound",
    }
    (run_dir / "sensor_measurement_metrics.json").write_text(json.dumps(sensor_metrics, indent=2)+"\n", encoding="utf-8")
    (run_dir / "estimator_truth_comparison.json").write_text(json.dumps(estimator, indent=2)+"\n", encoding="utf-8")
    (run_dir / "uncertainty_metrics.json").write_text(json.dumps(uncertainty, indent=2)+"\n", encoding="utf-8")
    (run_dir / "timing_latest_pose_control.json").write_text(json.dumps(timing_control, indent=2)+"\n", encoding="utf-8")
    result = {
        "run_id": run_dir.name, "profile_id": profile["profile_id"],
        "sensor_measurements": sensor_metrics, "estimator": estimator,
        "timing_latest_pose_control": timing_control, "uncertainty": uncertainty,
    }
    (run_dir / "phase3_run_summary.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


def latest_pose_control(
    run_dir: Path, estimates: list[dict[str, Any]], frame: LocalCoordinateFrame,
    latency_ms_override: float | None = None,
) -> dict[str, Any]:
    detections = _rows(run_dir / "perception_detections.jsonl")
    observations = {row["observation_id"]: row for row in _rows(run_dir / "target_observations.jsonl")}
    links_path = run_dir / "observation_detection_links.json"
    links = json.loads(links_path.read_text(encoding="utf-8")) if links_path.is_file() else {}
    truth = json.loads((run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").read_text(encoding="utf-8"))["survivors"]
    history = PoseHistory(history_seconds=1000, max_interpolation_gap_ms=250, max_extrapolation_ms=50)
    for row in estimates:
        if row.get("latitude") is None or row.get("host_monotonic_time_ns") is None:
            continue
        point = frame.to_enu(GeoPoint(
            latitude=row["latitude"], longitude=row["longitude"],
            altitude_m=frame.origin.altitude_m + row["relative_altitude_m"],
        ))
        gps = row.get("gps_measurement") or {}
        history.add(PoseSample(
            vehicle_id="drone_01", autopilot_boot_time_ns=row["ap_sim_source_time_ns"],
            host_monotonic_time_ns=row["host_monotonic_time_ns"], host_wall_time_ns=row["host_wall_time_ns"],
            latitude=row["latitude"], longitude=row["longitude"], relative_altitude_m=row["relative_altitude_m"],
            absolute_altitude_m=frame.origin.altitude_m + row["relative_altitude_m"],
            local_position_ned_m=(point.north_m, point.east_m, -point.up_m),
            quaternion_body_to_ned=row["quaternion_body_to_ned"], velocity_ned_m_s=row["velocity_ned_mps"],
            gps_fix=gps.get("fix_type"), satellites=gps.get("satellites"),
            horizontal_accuracy_m=gps.get("eph_m"), vertical_accuracy_m=gps.get("epv_m"),
            accuracy_sources={"horizontal": "GPS_RAW_INT_TELEMETRY", "vertical": "GPS_RAW_INT_TELEMETRY"},
            sim_source_time_ns=row["ap_sim_source_time_ns"], timestamp_source="SITL_SIM_SOURCE_SIDECAR",
        ))
    camera = CameraProfile.model_validate(json.loads((run_dir / "camera_profile.json").read_text(encoding="utf-8")))
    calibration, extrinsics = simulation_camera_geometry(camera)
    geolocator = TargetGeolocator(
        calibration=calibration, extrinsics=extrinsics, pose_history=history, clock_sync=None,
        ground_model=FlatGroundModel(0, "SIMULATION_FLAT_GROUND"),
        wgs84_converter=lambda east, north, up: frame.to_wgs84(EnuPoint(east, north, up)),
        uncertainty=GeolocationUncertaintyEstimator(1.5, 1, 2, .5, .25, .75, 1.645),
        minimum_gps_fix=3, maximum_view_angle_from_nadir_deg=70, require_sync=False,
        require_calibrated_extrinsics=False,
    )
    results: list[dict[str, Any]] = []
    by_frame = {row["frame_id"]: row for row in detections}
    estimates_with_host = [row for row in estimates if row.get("host_monotonic_time_ns") is not None]
    for observation_id, link in list(links.items())[:50]:
        frame_id, detection_index = link
        envelope = by_frame.get(frame_id)
        correct = observations.get(observation_id)
        if not envelope or not correct or envelope.get("camera_delivery_monotonic_ns") is None:
            continue
        receipt = (
            int(envelope["camera_sensor_received_monotonic_ns"] + latency_ms_override * 1e6)
            if latency_ms_override is not None and envelope.get("camera_sensor_received_monotonic_ns") is not None
            else int(envelope["camera_delivery_monotonic_ns"])
        )
        candidates = [row for row in estimates_with_host if int(row["host_monotonic_time_ns"]) <= receipt]
        if not candidates:
            continue
        latest = max(candidates, key=lambda row: int(row["host_monotonic_time_ns"]))
        restored = detection_result_from_dict(envelope)
        if detection_index >= len(restored.detections):
            continue
        located = geolocator.geolocate(
            restored, restored.detections[detection_index], "drone_01",
            active_width=restored.detections[detection_index].frame_width,
            active_height=restored.detections[detection_index].frame_height,
            active_sensor_mode="MONO8_SIMULATION", mapped_autopilot_time_ns=int(latest["ap_sim_source_time_ns"]),
        )
        if isinstance(located, RejectedObservation):
            continue
        target_truth = min(truth, key=lambda item: math.dist(
            (correct["target_east_m"], correct["target_north_m"]), item["position_enu_m"][:2]))
        correct_error = math.dist((correct["target_east_m"], correct["target_north_m"]), target_truth["position_enu_m"][:2])
        latest_error = math.dist((located.target_east_m, located.target_north_m), target_truth["position_enu_m"][:2])
        results.append({
            "frame_id": frame_id,
            "camera_latency_ms": (
                latency_ms_override if latency_ms_override is not None
                else envelope.get("camera_transport_latency_ms")
            ),
            "capture_ap_source_time_ns": correct["interpolated_pose"]["sample"]["autopilot_boot_time_ns"],
            "receipt_latest_ap_source_time_ns": latest["ap_sim_source_time_ns"],
            "correct_frame_time_error_m": correct_error, "latest_pose_error_m": latest_error,
            "latest_minus_correct_error_m": latest_error-correct_error,
        })
    return {
        "classification": "SIMULATION_EVALUATOR_ONLY_NEGATIVE_CONTROL",
        "autonomy_used_correct_frame_time": True, "latest_pose_fed_to_autonomy": False,
        "samples": results,
        "correct_frame_time_error_p50_m": _p([row["correct_frame_time_error_m"] for row in results], 50),
        "latest_pose_error_p50_m": _p([row["latest_pose_error_m"] for row in results], 50),
        "latest_minus_correct_error_p50_m": _p([row["latest_minus_correct_error_m"] for row in results], 50),
        "receipt_time_pose_fallbacks": 0, "latest_pose_fallbacks": 0,
        "host_authoritative_for_pose": False,
    }


_latest_pose_control = latest_pose_control


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
