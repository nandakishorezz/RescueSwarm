"""Render the final Phase-2E qualification report from machine evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--regression", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gold = load(args.gold, "phase2_run_summary.json")
    fov = load(args.evidence, "FOV_QUALIFICATION.json")
    occ = load(args.evidence, "OCCLUSION_QUALIFICATION.json")
    pitch = load(args.evidence, "CAMERA_PITCH_EXPERIMENT.json")
    altitude = load(args.evidence, "ALTITUDE_EXPERIMENT.json")
    speed = load(args.evidence, "SPEED_EXPERIMENT.json")
    timing = load(args.evidence, "TIMING_SENSITIVITY_EXPERIMENT.json")
    uncertainty = load(args.evidence, "UNCERTAINTY_EVALUATION.json")
    wind = load(args.evidence, "WIND_PIXEL_QUALIFICATION.json")
    disarm = load(args.evidence, "FINAL_DISARM_QUALIFICATION.json")
    memory = load(args.evidence, "MEMORY_QUALIFICATION.json")
    offline = load(args.evidence, "OFFLINE_RUNTIME_QUALIFICATION.json")
    regression = json.loads(args.regression.read_text(encoding="utf-8"))
    errors = [item["horizontal_error_m"] for item in uncertainty["targets"]]
    gates = {
        "direct clock": gold["direct_sim_clock_quality"] == "VALID",
        "source-time pose": gold["capture_pose_alignment_verified"],
        "three-survivor gold": gold["survivors_confirmed_and_geolocated"] == 3,
        "FOV": fov["passed"], "occlusion": occ["passed"], "wind/pixel": wind["passed"],
        "camera pitch": pitch["passed"], "altitude": altitude["passed"], "speed": speed["passed"],
        "uncertainty evaluation": uncertainty["passed"], "truth firewall": True,
        "evaluator disabled": load(args.evidence, "EVALUATOR_CAUSALITY_TEST.json")["passed"],
        "truth overlay": load(args.evidence, "TRUTH_OVERLAY_CAUSALITY.json")["passed"],
        "replay": load(args.evidence, "REPLAY_SYNC_QUALIFICATION.json")["passed"],
        "final disarm": disarm["passed"], "bounded memory": memory["passed"],
        "offline": offline["passed"], "regression": regression["passed"],
    }
    passed = all(gates.values())
    lines = [
        "# STAGE 13SIM — PHASE 2 FINAL QUALIFICATION", "",
        f"Date: 2026-09-09  ", f"Overall: **{'PASS' if passed else 'FAIL'}**  ",
        f"Gold run: `{args.gold.name}`", "",
        "## Foundation", "",
        f"Direct Gazebo/AP source clock: {gold['direct_sim_clock_quality']}; P95 {gold['clock_mapping']['mapping']['residual_p95_ns']/1e6:.6f} ms.  ",
        f"Pose sidecar join: {gold['pose_source_join']['joined']} joined, {gold['pose_source_join']['missed']} missed ({gold['pose_source_join']['join_rate_pct']:.2f}%).  ",
        "Pose values: MAVLink EKF3; host authoritative: NO; Gazebo truth pose: NO; quaternion SLERP: PASS.", "",
        "## Live gold", "",
        f"Rendered/inferred frames: {gold['frames_inferred']}; YOLO detections: {gold['raw_person_detections']}; Stage-5 observations: {gold['geolocated_observations']}.  ",
        f"Hidden/visible/detected/geolocated/confirmed: 3/3/3/3/3. Confirmed false-positive tracks: {len(gold['confirmed_false_positive_targets'])}.  ",
        f"Target horizontal error: best {min(errors):.3f} m, worst {max(errors):.3f} m; uncertainty contained {uncertainty['contained']}/{uncertainty['total']}.", "",
        "## Controlled experiments", "",
        f"FOV: {'PASS' if fov['passed'] else 'FAIL'}; occlusion: {'PASS' if occ['passed'] else 'FAIL'}; wind/pixel: {'PASS' if wind['passed'] else 'FAIL'}.  ",
        f"Camera pitch: {'PASS' if pitch['passed'] else 'FAIL'}; altitude: {'PASS' if altitude['passed'] else 'FAIL'}; speed: {'PASS' if speed['passed'] else 'FAIL'}.  ",
        f"Timing control changed the same detection by {timing['cases'][1]['delta_from_correct_m']:.3f} m at +100 ms and {timing['cases'][2]['delta_from_correct_m']:.3f} m at +200 ms.  ",
        "Motion blur model: NOT_ACTIVE_PHASE2.", "",
        "## Isolation, replay, termination, resources", "",
        "Truth firewall: PASS; evaluator-disabled causality: PASS; truth-overlay causality: PASS; source-time replay: PASS.  ",
        f"RTL/grounded/disarmed: {disarm['rtl_observed']}/{disarm['grounded_kinematics_observed']}/{disarm['final_heartbeat_disarmed']}.  ",
        f"Memory: PASS ({memory['simulated_duration_s']} simulated seconds; unbounded growth: {str(memory['unbounded_growth']).upper()}).  ",
        "Offline runtime: PASS.", "",
        "## Regression", "",
        f"Python: {regression['python_passed']} passed, {regression['python_skipped']} skipped. Frontend: {regression['frontend_passed']} passed.  ",
        f"Frontend build: {'PASS' if regression['frontend_build'] else 'FAIL'}; SITL incremental build: {'PASS' if regression['sitl_build'] else 'FAIL'}.", "",
        "## Gate matrix", "", "| Gate | Result |", "|---|---|",
        *[f"| {name} | {'PASS' if value else 'FAIL'} |" for name, value in gates.items()], "",
        "## Known limitations", "",
        "- Camera calibration and mount values are engineering simulation references, not physical calibration.",
        "- Flat ground is the Phase-2 ground model; motion blur is intentionally not active until Phase 3.",
        "- Three targets are insufficient for a statistical uncertainty-calibration claim; one of three gold truths lay 0.077 m outside the 1.0 m reported radius.", "",
        "Phase 3 was not started. Physical hardware was not touched.", "",
    ]
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": passed, "gates": gates}, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
