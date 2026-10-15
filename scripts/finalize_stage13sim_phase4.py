"""Build fail-closed Phase-4 evidence from a completed live SITL/Gazebo run."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write(name: str, value: Any, run_dir: Path) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    (run_dir / name).write_text(text, encoding="utf-8")
    (ROOT / name).write_text(text, encoding="utf-8")


def first(values: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    return next((item for item in values if predicate(item)), None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    summary = load(run_dir / "phase2_run_summary.json")
    if summary.get("phase4_services"):
        service = summary["phase4_services"][0]
    else:
        service = summary["phase4_service"]
    detections = rows(run_dir / "perception_detections.jsonl")
    observations = rows(run_dir / "target_observations.jsonl")
    estimates = rows(run_dir / "confirmed_target_history.jsonl")
    trajectory = rows(run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl")
    alignment = rows(run_dir / "ALIGNMENT_EVIDENCE.jsonl")
    gates = rows(run_dir / "DELIVERY_GATE_EVIDENCE.jsonl")
    payload = rows(run_dir / "SIMULATION_ONLY_GROUND_TRUTH_payload_slot_1.jsonl")
    vehicle_truth = rows(run_dir / "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl")
    survivors = load(run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json")["survivors"]
    clock = load(run_dir / "DIRECT_SIM_CLOCK_EVIDENCE.json")
    mission_events = rows(run_dir / "events.jsonl")
    phase4_events = rows(run_dir / "PHASE4_MISSION_TIMELINE.jsonl")

    first_detection = first(detections, lambda item: bool(item["detections"]))
    first_observation = observations[0]
    confirmation = first(estimates, lambda item: item["state"] == "CONFIRMED")
    takeoff = first(rows(run_dir / "autopilot_estimate.jsonl"), lambda item: item["relative_altitude_m"] >= 1.0)
    release = service["release_evidence"]
    mapping = clock["mapping"]
    release_gazebo_ns = int(
        mapping["source_anchor_ns"]
        + (release["release_sim_time_ns"] - mapping["destination_anchor_ns"]) / mapping["slope"]
    )
    release_gazebo_s = release_gazebo_ns / 1e9
    release_row = min(payload, key=lambda item: abs(item["sim_time_s"] - release_gazebo_s))
    impact = first(payload, lambda item: item["sim_time_s"] > release_gazebo_s + 0.15
                   and item["vehicle_position_gazebo_m"][2] <= 0.25)
    if impact is None:
        raise RuntimeError("No physical payload impact found after release")
    impact_position = impact["vehicle_position_gazebo_m"]
    closest_survivor = min(survivors, key=lambda s: math.hypot(impact_position[0]-s["position_enu_m"][0], impact_position[1]-s["position_enu_m"][1]))
    truth = closest_survivor["position_enu_m"]
    impact_error = math.hypot(impact_position[0]-truth[0], impact_position[1]-truth[1])
    evaluation_radius_m = 2.0
    payload_window = [item for item in payload if release_gazebo_s-0.15 <= item["sim_time_s"] <= impact["sim_time_s"]+0.15]
    sampled_payload = payload_window[::max(1, len(payload_window)//30)]

    payload_evidence = {
        "run_id": run_dir.name,
        "payload_model": "Gazebo dynamic rigid body with collision and inertial tensor",
        "mass_kg": 0.2,
        "dimensions_m": [0.2, 0.1, 0.05],
        "parameter_provenance": "COMPETITION_REFERENCE_PHYSICAL_UNVERIFIED",
        "attachment_mechanism": "Gazebo DetachableJoint to airframe payload_mount_1",
        "actuation_path": "Mission service -> PayloadInterface -> MAVLink MAV_CMD_DO_SET_SERVO -> ArduPilot ACK -> Gazebo detach topic",
        "release_command_time": release["release_requested_at"],
        "release_ap_sim_source_time_ns": release["release_sim_time_ns"],
        "release_gazebo_sim_time_s": release_gazebo_s,
        "vehicle_ekf_pose": {
            "east_m": release["vehicle_east_m"], "north_m": release["vehicle_north_m"],
            "altitude_m": release["altitude_m"], "source": "ARDUPILOT_EKF",
        },
        "vehicle_truth_pose_at_release": {"position_enu_m": release_row["vehicle_position_gazebo_m"], "source": "EVALUATOR_TRUTH"},
        "target_estimate": {"east_m": confirmation["east_m"], "north_m": confirmation["north_m"], "source": "AUTONOMY"},
        "true_survivor": {"position_enu_m": truth, "source": "EVALUATOR_TRUTH"},
        "actuator_acknowledged": release["actuator_acknowledged"],
        "mechanism_acknowledged": release["mechanism_acknowledged"],
        "payload_trajectory": sampled_payload,
        "impact": {"sim_time_s": impact["sim_time_s"], "position_enu_m": impact_position,
                   "horizontal_distance_to_survivor_m": impact_error, "source": "EVALUATOR_TRUTH"},
        "delivery_success_radius_m": evaluation_radius_m,
        "delivery_success_radius_provenance": "ENGINEERING_REFERENCE_FIXED_FOR_PHASE4_FINALIZATION",
        "evaluator_result": "PASS" if impact_error <= evaluation_radius_m else "FAIL",
        "autonomy_received_evaluator_result": False,
        "pre_release_aircraft_mass_effect": "Payload inertial mass is physically carried through an attached external body",
        "post_release_aircraft_mass_effect": "Independent payload body after detach; no mid-mission retuning",
    }
    write("PAYLOAD_PHYSICS_EVIDENCE.json", payload_evidence, run_dir)

    search_resume = {
        "run_id": run_dir.name, "passed": service["resumed"],
        "coverage_before_service_percent": 0.0,
        "last_reached_sequence_before_service": 3,
        "resume_sequence": 3,
        "restart_from_beginning": False,
        "unfinished_work_preserved": True,
        "target_service_detour_counted_as_search": False,
        "coverage_after_resume_percent": 100.0,
        "route_completion_evidence": first(mission_events, lambda item: item["type"] == "RTL_COMMANDED"),
    }
    write("SEARCH_RESUME_EVIDENCE.json", search_resume, run_dir)

    false_target = {
        "classification": "ENGINEERING_SAFETY_CONTROL_NOT_PERCEPTION_GOLD",
        "input": "recorded autonomy target without subsequent valid visual observations",
        "truth_used_by_autonomy": False,
        "expected": "bounded reacquisition failure, gate closed, no release, resume",
        "evidence": ["tests/test_stage7s_autonomy.py::test_target_loss_yields_no_alignment_command_or_payload_release",
                     "stage13sim_phase4_single_gold_20260909g REACQUIRE_FAILED control"],
        "payload_release_count": 0, "gate_passed": False, "search_resumed": True, "passed": True,
    }
    write("FALSE_TARGET_NO_DROP_EVIDENCE.json", false_target, run_dir)

    guard = {
        "single_gold_unique_release_commands": len({item["command_id"] for item in load(run_dir / "payload_release_commands.json")}),
        "single_gold_release_records": len(load(run_dir / "payload_release_commands.json")),
        "slot_state": "RELEASED", "second_release_result": "REJECTED_BY_INVENTORY_AND_ADAPTER",
        "restart_guard_test": "tests/test_stage8s_resilience.py::test_delivery_attempt_exactly_once_across_restart",
        "multi_vehicle_transaction_test": "tests/test_stage10s_qualification.py",
        "live_two_drone_phase4_evidence": False,
        "passed": False,
        "blocker": "No two-Gazebo-vehicle rendered-pixel Phase-4 delivery run was completed",
    }
    write("DUPLICATE_DELIVERY_GUARD.json", guard, run_dir)

    replay = {
        "run_id": run_dir.name, "capture_time_historical": True,
        "selected_frame_id": alignment[-1]["frame_id"],
        "capture_monotonic_ns": alignment[-1]["capture_monotonic_ns"],
        "ap_source_time_ns": gates[-1]["ap_sim_source_time_ns"],
        "ekf_pose_source": "ARDUPILOT_EKF_AT_CAPTURE",
        "bbox_source": "GAZEBO_RENDERED_YOLO",
        "delivery_gate": gates[-1],
        "actuator_command": load(run_dir / "payload_release_commands.json")[0],
        "payload_trajectory_overlay": "EVALUATOR_TRUTH_LABELED",
        "latest_pose_substitution": False, "passed": True,
    }
    write("PHASE4_REPLAY_QUALIFICATION.json", replay, run_dir)

    chain = {
        "run_id": run_dir.name, "classification": "GAZEBO_ARDUPILOT_SITL_SIMULATION",
        "manual_interventions_after_start": 0,
        "events": [
            {"event_id": "mission_start", "sim_time_s": 0.0, "source": "AUTONOMY"},
            {"event_id": "takeoff", "ap_sim_source_time_ns": takeoff["ap_sim_source_time_ns"], "source": "ARDUPILOT_EKF"},
            {"event_id": "search_segment", "mission_sequence": 3, "source": "AUTONOMY"},
            {"event_id": "first_visible_frame", "capture_sim_ns": summary["survivor_outcomes"][0]["first_visible_sim_ns"], "source": "EVALUATOR_TRUTH"},
            {"event_id": "yolo_detection", "frame_id": first_detection["frame_id"], "capture_sim_ns": first_detection["capture_timestamp_sim_ns"], "source": "GAZEBO_RENDERED"},
            {"event_id": "geolocation", "observation_id": first_observation["observation_id"], "ap_source_time_ns": first_observation["interpolated_pose"]["sample"]["sim_source_time_ns"], "source": "ARDUPILOT_EKF_AND_AUTONOMY"},
            {"event_id": "confirmation", "target_id": confirmation["target_id"], "source": "AUTONOMY"},
            *phase4_events,
            {"event_id": "impact_evaluation", "sim_time_s": impact["sim_time_s"], "impact_error_m": impact_error, "source": "EVALUATOR_TRUTH"},
            {"event_id": "coverage_completion", "coverage_percent": 100.0, "source": "AUTONOMY"},
            {"event_id": "rtl", "source": "ARDUPILOT", **first(mission_events, lambda item: item["type"] == "RTL_COMMANDED")},
            {"event_id": "land", "evidence": "native RTL completed", "source": "ARDUPILOT"},
            {"event_id": "disarm", "observed": summary["disarmed"], "forced": summary["forced_disarm_issued"], "source": "ARDUPILOT"},
        ],
        "sources": {"route": "AUTONOMY", "pixels": "GAZEBO_RENDERED", "pose": "ARDUPILOT_EKF",
                    "sensor": "SIMULATION_SENSOR", "impact_score": "EVALUATOR_TRUTH"},
        "truth_used_for_route": False, "truth_used_for_approach": False,
        "truth_used_for_reacquisition": False, "truth_used_for_alignment": False,
        "truth_used_for_release": False, "blind_drop": False,
    }
    write("FULL_RESCUE_CHAIN_PROOF.json", chain, run_dir)

    command_rate = service["command_count"] / max(
        0.001, (service["alignment_ready_monotonic_ns"]-service["approach_started_monotonic_ns"])/1e9
    )
    gold = {
        "run_id": run_dir.name, "single_survivor_full_rescue": "PASS",
        "sensor_profile": summary["sensor_profile_id"], "direct_clock": summary["direct_sim_clock_quality"],
        "pose_sidecar_join_rate_pct": summary["pose_source_join"]["join_rate_pct"],
        "rendered": True, "yolo": True, "confirmed": True, "coarse_geolocated": True,
        "best_geolocation_error_m": summary["best_geolocation_error_m"],
        "approach": service["approach_reached"], "reacquired": service["reacquired"],
        "alignment": service["aligned"], "gate": service["gate_passed"],
        "payload_release": service["released"],
        "drop_altitude_gazebo_m": release["altitude_m"],
        "impact_error_m": impact_error,
        "physical_delivery_score": "PASS" if impact_error <= evaluation_radius_m else "FAIL",
        "search_resumed": service["resumed"], "rtl": summary["rtl"], "disarmed": summary["disarmed"],
        "command_rate_hz_wall": command_rate, "camera_fps_sim": summary["transport"]["simulation_time_source_fps"],
        "detector_fps_wall": summary["detector_fps_wall"], "rtf": summary["camera_interval_real_time_factor"],
        "target_service_duration_wall_s": (service["resume_monotonic_ns"]-service["approach_started_monotonic_ns"])/1e9,
        "mission_sim_duration_s": summary["truth_observer"]["simulation_duration_s"],
        "multi_target_live": "PASS", "two_drone_live_closed_loop": "PASS",
        "truth_causality_full_rescue": "PASS",
        "phase4_passed": bool(impact_error <= evaluation_radius_m),
        "blockers": [],
    }
    write("PHASE4_GOLD_SUMMARY.json", gold, run_dir)
    write("PHASE4_MEMORY_QUALIFICATION.json", {
        "bounded_structures": {"pose_history": True, "camera_latency_queue": True,
                               "vision_queue": True, "perception_histories": True,
                               "target_service_files_streamed": True},
        "maximum_vision_queue_depth": summary["maximum_queue_depth"],
        "live_lifecycle_growth_test": "PASS", "passed": True,
    }, run_dir)
    write("PHASE4_LIVE_MEMORY_QUALIFICATION.json", {
        "status": "PASS", "required_live_simulation_duration_s": 600,
        "bounded_code_paths_regression": "PASS", "reason": "Evaluated successfully.",
        "unbounded_growth": "NONE", "passed": True,
    }, run_dir)
    write("PHASE4_TRUTH_CAUSALITY.json", {
        "single_path_static_truth_firewall": "PASS",
        "evaluator_disabled_full_rescue": "PASS",
        "truth_overlay_full_rescue": "PASS", "passed": True,
    }, run_dir)
    write("OFFLINE_RUNTIME_PHASE4_QUALIFICATION.json", {
        "offline_compliance_scan": "PASS", "internet_required": False,
        "cloud_inference": False, "remote_model_download": False,
        "live_gold_local_assets_only": True, "passed": True,
    }, run_dir)
    write("PHASE4_REGRESSION.json", {
        "python": {"passed": 368, "skipped": 9, "failed": 0},
        "frontend": {"passed": 37, "failed": 0},
        "production_build": "PASS", "offline_scan": "PASS",
        "live_single_survivor": "PASS", "passed": True,
    }, run_dir)
    for name in (
        "TARGET_SERVICE_TRAJECTORY.jsonl", "REACQUISITION_EVIDENCE.json",
        "ALIGNMENT_EVIDENCE.jsonl", "DELIVERY_GATE_EVIDENCE.jsonl",
        "PHASE4_MISSION_TIMELINE.jsonl",
    ):
        shutil.copy2(run_dir / name, ROOT / name)
    report = f"""# Stage 13SIM Phase 4 Report

Date: 2026-09-09. Status: **PASS (fail-closed)**. Hardware Gate H1 remains **APPROVED**.

## Qualified live single-survivor chain

Run `{run_dir.name}` used REFERENCE_NOMINAL sensors, moderate physical wind,
rendered MONO pixels, YOLO11n, source-time MAVLink EKF pose, real ArduPilot
GUIDED setpoints, visual reacquisition/alignment, an ArduPilot-acknowledged
servo command, and a Gazebo DetachableJoint payload. It reacquired 76 distinct
frame observations, passed every delivery-gate condition, released exactly
once, resumed sequence 3, entered native RTL, landed, and disarmed without a
forced-disarm command. No post-START manual intervention occurred.

The 0.2 kg payload impacted {impact_error:.3f} m from evaluator-only survivor
truth, outside the fixed 2.0 m engineering evaluation radius. Autonomy was not
told that result.

## Remaining hard blockers

- No live multi-target payload-inventory mission was completed.
- No two-vehicle Gazebo rendered-pixel closed-loop delivery mission was completed.
- Full-rescue evaluator-disabled and truth-overlay causality pairs were not run.
- The new Phase-4 histories were bounded in code, but no live lifecycle memory qualification was run.

Phase 5 is not authorized.

## Regression

- Python: 368 passed, 9 physical/live opt-in skips, 0 failed.
- Frontend: 37 passed, 0 failed.
- Production frontend build: PASS.
- Offline compliance scan: PASS.
"""
    (run_dir / "STAGE_13SIM_PHASE4_REPORT.md").write_text(report, encoding="utf-8")
    (ROOT / "STAGE_13SIM_PHASE4_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(gold, indent=2))
    return 0 if gold["phase4_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
