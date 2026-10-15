"""Run the Stage 7S search fixture without a rescue diversion and compare it.

This is the control experiment for coverage continuity.  It uses the exact
boundary, planner inputs, ArduCopter build, launcher speed-up, and local SITL
endpoint used by the Stage 7S gold rescue mission, but never leaves AUTO for a
target service.  No physical connection is accepted by SITLAutopilotAdapter.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.artifacts import Stage3ArtifactWriter
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.runner import SITLMissionRunner


ROOT = Path(__file__).resolve().parents[1]
MESSAGE_RATES_HZ = {
    "GLOBAL_POSITION_INT": 10,
    "LOCAL_POSITION_NED": 10,
    "ATTITUDE_QUATERNION": 10,
    "ATTITUDE": 10,
    "GPS_RAW_INT": 5,
    "SYS_STATUS": 2,
    "MISSION_CURRENT": 3,
    "EXTENDED_SYS_STATE": 2,
    "EKF_STATUS_REPORT": 2,
    "HOME_POSITION": 1,
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--ardupilot-home", default=os.getenv("ARDUPILOT_HOME", "/home/nandakishore7/ardupilot"))
    value.add_argument("--speedup", type=int, default=3)
    value.add_argument("--connection", default="tcp:127.0.0.1:5760")
    return value


def gold_fixture() -> tuple[GeoPoint, Boundary, Any]:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
        source_format="stage7s_gold_fixture",
        area_m2=1176,
        area_hectares=.1176,
        origin=origin,
    )
    plan = CoveragePlanner().plan(
        "stage7s-uninterrupted-control",
        boundary,
        CoverageRequest(
            search_altitude_m=10,
            lane_spacing_m=8,
            effective_swath_m=8,
            edge_margin_m=2,
            search_speed_m_s=6,
            min_planned_coverage_percent=98,
        ),
    )
    return origin, boundary, plan


def _duration_seconds(execution: dict[str, Any]) -> float | None:
    if not execution.get("started_at") or not execution.get("completed_at"):
        return None
    started = datetime.fromisoformat(execution["started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(execution["completed_at"].replace("Z", "+00:00"))
    return (completed - started).total_seconds()


def _comparison(control: dict[str, Any], rescue: dict[str, Any]) -> dict[str, Any]:
    control_duration = _duration_seconds(control)
    rescue_duration = _duration_seconds(rescue)
    return {
        "experiment": "same_plan_uninterrupted_control_vs_stage7s_rescue",
        "generated_at": datetime.now(UTC).isoformat(),
        "coverage_counts_searching_trail_only": True,
        "control": {
            "coverage_percent": control["actual_coverage_percent"],
            "missed_area_m2": control["missed_area_m2"],
            "overlap_area_m2": control["overlap_area_m2"],
            "total_path_length_m": control["actual_path_length_m"],
            "duration_s": control_duration,
        },
        "rescue": {
            "coverage_percent": rescue["actual_coverage_percent"],
            "missed_area_m2": rescue["missed_area_m2"],
            "overlap_area_m2": rescue["overlap_area_m2"],
            "total_path_length_m": rescue["actual_path_length_m"],
            "duration_s": rescue_duration,
        },
        "difference_rescue_minus_control": {
            "coverage_percentage_points": rescue["actual_coverage_percent"] - control["actual_coverage_percent"],
            "missed_area_m2": rescue["missed_area_m2"] - control["missed_area_m2"],
            "overlap_area_m2": rescue["overlap_area_m2"] - control["overlap_area_m2"],
            "additional_total_distance_m": rescue["actual_path_length_m"] - control["actual_path_length_m"],
            "additional_mission_time_s": (
                rescue_duration - control_duration
                if rescue_duration is not None and control_duration is not None
                else None
            ),
        },
        "continuity_verdict": (
            "PASS"
            if rescue["actual_coverage_percent"] >= 98.0 and rescue["missed_area_m2"] <= 5.0
            else "FAIL"
        ),
        "thresholds": {
            "minimum_final_search_coverage_percent": 98.0,
            "maximum_missed_area_m2": 5.0,
            "scope": "SITL_ENGINEERING_ONLY",
        },
    }


def _markdown(comparison: dict[str, Any]) -> str:
    control = comparison["control"]
    rescue = comparison["rescue"]
    delta = comparison["difference_rescue_minus_control"]
    return (
        "# Stage 7S coverage continuity control\n\n"
        f"- Verdict: {comparison['continuity_verdict']}\n"
        "- Method: identical coverage plan; uninterrupted AUTO control compared with rescue-interrupted run\n"
        "- Coverage accounting: SEARCHING trail only; GUIDED diversion and RTL excluded\n\n"
        "| Metric | Uninterrupted | Rescue interrupted | Difference |\n"
        "|---|---:|---:|---:|\n"
        f"| Search coverage | {control['coverage_percent']:.3f}% | {rescue['coverage_percent']:.3f}% | {delta['coverage_percentage_points']:+.3f} pp |\n"
        f"| Missed area | {control['missed_area_m2']:.3f} m2 | {rescue['missed_area_m2']:.3f} m2 | {delta['missed_area_m2']:+.3f} m2 |\n"
        f"| Repeated/overlap area | {control['overlap_area_m2']:.3f} m2 | {rescue['overlap_area_m2']:.3f} m2 | {delta['overlap_area_m2']:+.3f} m2 |\n"
        f"| Total flown distance | {control['total_path_length_m']:.3f} m | {rescue['total_path_length_m']:.3f} m | {delta['additional_total_distance_m']:+.3f} m |\n"
        f"| Mission time | {control['duration_s']:.3f} s | {rescue['duration_s']:.3f} s | {delta['additional_mission_time_s']:+.3f} s |\n"
    )


def main() -> int:
    args = parser().parse_args()
    latest_path = ROOT / "data" / "rescue_runs" / "latest.json"
    if not latest_path.is_file():
        raise FileNotFoundError("Run scripts/run_stage7s_sitl_rescue.py before the continuity control")
    rescue_latest = json.loads(latest_path.read_text(encoding="utf-8"))
    origin, boundary, plan = gold_fixture()
    launcher = SITLLauncher(ardupilot_home=args.ardupilot_home, speedup=args.speedup)
    print("[1/5] Starting the identical uninterrupted ArduCopter control mission")
    identity = launcher.start(origin, 0, ROOT / "data" / "rescue_baselines" / "sitl_launcher.log")
    artifacts = Stage3ArtifactWriter(ROOT / "data" / "rescue_baselines", plan.mission_id)
    adapter = SITLAutopilotAdapter(args.connection, "sitl", "simulation_only", 60, 15)
    runner = SITLMissionRunner(adapter, 60, 480, 180, MESSAGE_RATES_HZ, artifacts=artifacts)
    try:
        execution = runner.execute(plan, boundary, identity.commit)
    finally:
        launcher.stop()
    if execution.state.value != "COMPLETED":
        print("FAIL: uninterrupted control did not complete", execution.errors)
        return 1
    print("[2/5] Uninterrupted AUTO/RTL/LAND control completed")
    control = execution.model_dump(mode="json")
    comparison = _comparison(control, rescue_latest["execution"])
    report = _markdown(comparison)
    comparison_path = Path(rescue_latest["run_directory"]) / "coverage_continuity.json"
    comparison_path.write_text(json.dumps(comparison, indent=2, sort_keys=True), encoding="utf-8")
    (Path(rescue_latest["run_directory"]) / "coverage_continuity_report.md").write_text(report, encoding="utf-8")
    (ROOT / "data" / "rescue_baselines" / "latest.json").write_text(
        json.dumps({"run_directory": str(artifacts.run_directory), "execution": control}, indent=2), encoding="utf-8"
    )
    print("[3/5] Search-only coverage compared against the rescue mission")
    root_report = ROOT / "STAGE_7S_REPORT.md"
    existing = root_report.read_text(encoding="utf-8") if root_report.is_file() else "# Stage 7S report\n"
    marker = "\n## Coverage continuity control\n"
    if marker in existing:
        existing = existing.split(marker, 1)[0].rstrip() + "\n"
    root_report.write_text(existing + marker + "\n" + report.removeprefix("# Stage 7S coverage continuity control\n\n"), encoding="utf-8")
    print(f"[4/5] Evidence: {comparison_path}")
    print(
        "[5/5]",
        comparison["continuity_verdict"],
        f"coverage={comparison['rescue']['coverage_percent']:.3f}%",
        f"extra_distance={comparison['difference_rescue_minus_control']['additional_total_distance_m']:.3f}m",
    )
    return 0 if comparison["continuity_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
