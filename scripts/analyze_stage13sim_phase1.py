"""Derive reproducible observer evidence from Stage-13SIM live artifacts."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame


def qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw)


def qrotate(q, value):
    w, x, y, z = q
    result = qmul(qmul(q, (0.0, *value)), (w, -x, -y, -z))
    return result[1:]


def qangle(a, b):
    dot = abs(sum(x*y for x, y in zip(a, b))) / math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return math.degrees(2 * math.acos(min(1.0, dot)))


def roll_pitch(q):
    w, x, y, z = q
    return (
        math.degrees(math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y))),
        math.degrees(math.asin(max(-1.0, min(1.0, 2*(w*y-z*x))))),
    )


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def footprint_offset(vehicle_position, vehicle_q_flu_to_enu, camera):
    # Generated model pose is FLU; Stage-5 body is FRD.
    body_q = qmul(vehicle_q_flu_to_enu, (0.0, 1.0, 0.0, 0.0))
    mount = camera["mount_translation_body_frd"]["value"]
    origin = tuple(vehicle_position[i] + qrotate(body_q, mount)[i] for i in range(3))
    camera_q = qmul(body_q, camera["mount_rotation_camera_to_body"]["value"])
    intr = camera["intrinsics"]
    fx, fy, cx, cy = (intr[key]["value"] for key in ("fx", "fy", "cx", "cy"))
    points = []
    for u, v in ((0, 0), (camera["resolution_width"], 0), (camera["resolution_width"], camera["resolution_height"]), (0, camera["resolution_height"])):
        ray = qrotate(camera_q, ((u-cx)/fx, (v-cy)/fy, 1.0))
        if ray[2] >= -1e-6:
            return None
        factor = -origin[2] / ray[2]
        points.append((origin[0] + factor*ray[0], origin[1] + factor*ray[1]))
    center = tuple(sum(point[index] for point in points)/4 for index in range(2))
    return (center[0]-vehicle_position[0], center[1]-vehicle_position[1])


def analyze(run_dir: Path):
    estimates = read_jsonl(run_dir / "autopilot_estimate.jsonl")
    truth = read_jsonl(run_dir / "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl")
    camera = json.loads((run_dir / "camera_profile.json").read_text(encoding="utf-8"))
    mission = json.loads((run_dir / "georeference.json").read_text(encoding="utf-8"))
    origin = mission["origin_wgs84"]
    frame = LocalCoordinateFrame(GeoPoint(latitude=origin[0], longitude=origin[1], altitude_m=origin[2]))
    airborne = [item for item in estimates if item["relative_altitude_m"] > 5]
    speeds = [math.hypot(*item["velocity_ned_mps"][:2]) for item in airborne]
    rp = [roll_pitch(item["quaternion_body_to_ned"]) for item in airborne]
    motor_values = [value for item in airborne for value in (item.get("motor_outputs") or []) if value]
    truth_airborne = [item for item in truth if item["vehicle_position_gazebo_m"][2] > 5]
    camera_offsets = [value for item in truth_airborne if (value := footprint_offset(item["vehicle_position_gazebo_m"], item["vehicle_quaternion_gazebo"], camera)) is not None]
    camera_angles = [qangle(truth_airborne[0]["camera_quaternion_gazebo"], item["camera_quaternion_gazebo"]) for item in truth_airborne] if truth_airborne else []

    position_errors, attitude_errors = [], []
    if truth and estimates and any("simulation_elapsed_s" in item for item in estimates):
        truth_start = truth[0]["sim_time_s"]
        for estimate in estimates[::10]:
            elapsed = estimate.get("simulation_elapsed_s")
            if elapsed is None:
                continue
            target = truth_start + elapsed
            actual = min(truth, key=lambda item: abs(item["sim_time_s"]-target))
            wgs = frame.to_enu(GeoPoint(latitude=estimate["latitude"], longitude=estimate["longitude"], altitude_m=origin[2]+estimate["relative_altitude_m"]))
            position_errors.append(math.dist((wgs.east_m, wgs.north_m, wgs.up_m), actual["vehicle_position_gazebo_m"]))
            q_ned_to_enu = (0.0, math.sqrt(.5), math.sqrt(.5), 0.0)
            estimated_q = qmul(q_ned_to_enu, estimate["quaternion_body_to_ned"])
            truth_body_q = qmul(actual["vehicle_quaternion_gazebo"], (0.0, 1.0, 0.0, 0.0))
            attitude_errors.append(qangle(estimated_q, truth_body_q))

    result = {
        "classification": "SIMULATION_OBSERVER_EVALUATION_ONLY",
        "run_id": run_dir.name,
        "estimate_samples": len(estimates), "truth_samples": len(truth),
        "measured_ground_speed_mps": {
            "median": statistics.median(speeds) if speeds else None,
            "p95": sorted(speeds)[round(.95*(len(speeds)-1))] if speeds else None,
            "maximum": max(speeds) if speeds else None,
        },
        "airborne_roll_peak_to_peak_deg": max((x[0] for x in rp), default=0)-min((x[0] for x in rp), default=0),
        "airborne_pitch_peak_to_peak_deg": max((x[1] for x in rp), default=0)-min((x[1] for x in rp), default=0),
        "motor_output_range_pwm": [min(motor_values), max(motor_values)] if motor_values else None,
        "camera_world_quaternion_change_max_deg": max(camera_angles, default=0),
        "camera_footprint_relative_displacement_max_m": max((math.dist(camera_offsets[0], item) for item in camera_offsets), default=0) if camera_offsets else None,
        "position_discrepancy_m": {"median": statistics.median(position_errors), "maximum": max(position_errors)} if position_errors else None,
        "attitude_discrepancy_deg": {"median": statistics.median(attitude_errors), "maximum": max(attitude_errors)} if attitude_errors else None,
        "truth_firewall": "observer/evaluator only",
    }
    (run_dir / "coupling_evidence.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--calm-run", type=Path)
    args = parser.parse_args()
    result = analyze(args.run_dir)
    if args.calm_run:
        calm = analyze(args.calm_run)
        result["calm_comparison"] = {
            "calm_run": args.calm_run.name,
            "roll_peak_to_peak_delta_deg": result["airborne_roll_peak_to_peak_deg"]-calm["airborne_roll_peak_to_peak_deg"],
            "pitch_peak_to_peak_delta_deg": result["airborne_pitch_peak_to_peak_deg"]-calm["airborne_pitch_peak_to_peak_deg"],
        }
        (args.run_dir / "coupling_evidence.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
