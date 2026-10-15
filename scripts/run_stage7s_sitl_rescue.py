"""Run the deterministic Stage 7S gold mission against local official ArduCopter SITL."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.rescue_artifacts import Stage7SArtifactWriter
from ground.backend.app.sitl.rescue_runner import SITLRescueMissionRunner
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.models import SyntheticSurvivorScenario


ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--ardupilot-home", default=os.getenv("ARDUPILOT_HOME", "/home/nandakishore7/ardupilot"))
    value.add_argument("--target-east", type=float, default=20)
    value.add_argument("--target-north", type=float, default=14)
    value.add_argument("--speedup", type=int, default=3)
    value.add_argument("--connection", default="tcp:127.0.0.1:14552")
    return value


def main() -> int:
    args = parser().parse_args()
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
        source_format="stage7s_gold_fixture", area_m2=1176, area_hectares=.1176, origin=origin,
    )
    print("[1/10] Boundary created and validated")
    plan = CoveragePlanner().plan("stage7s-gold", boundary, CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))
    print(f"[2/10] Coverage plan generated: {len(plan.path_points_enu)} points, {plan.validation.planned_coverage_percent:.2f}%")
    launcher = SITLLauncher(ardupilot_home=args.ardupilot_home, speedup=args.speedup)
    identity = launcher.start(origin, 0, ROOT / "data" / "rescue_runs" / "sitl_launcher.log")
    print(f"[3/10] Official ArduCopter SITL started: {identity.commit or 'UNKNOWN'}")
    artifacts = Stage7SArtifactWriter(ROOT / "data", plan.mission_id)
    scenario = SyntheticSurvivorScenario(
        target_id="synthetic_survivor_01", true_east_m=args.target_east,
        true_north_m=args.target_north, visibility_distance_m=30, seed=7,
    )
    adapter = SITLAutopilotAdapter(args.connection, "sitl", "simulation_only", 150, 15)
    runner = SITLRescueMissionRunner(
        adapter, load_rescue_config(ROOT / "config" / "rescue_orchestration.yaml"),
        60, 480, 180,
        {
            "GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10,
            "ATTITUDE_QUATERNION": 10, "ATTITUDE": 10, "GPS_RAW_INT": 5,
            "SYS_STATUS": 2, "MISSION_CURRENT": 3, "EXTENDED_SYS_STATE": 2,
            "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1,
        }, artifacts=artifacts,
        event_callback=lambda event: print(f"[{event.type}] {event.message}"),
    )
    print("[4/10] Connecting, checking health, and uploading verified integer mission")
    try:
        execution = runner.execute(plan, boundary, scenario, identity.commit)
    finally:
        launcher.stop()
    print("[5/10] AUTO search and Stage 5 synthetic confirmation processed")
    print("[6/10] GUIDED approach and relative alignment processed")
    print("[7/10] Simulated payload release and verification processed")
    print("[8/10] AUTO checkpoint resume and coverage continuation processed")
    print("[9/10] RTL, landing, and disarm monitored")
    run_report = artifacts.run_directory / "stage7s_report.md"
    if run_report.is_file():
        shutil.copyfile(run_report, ROOT / "STAGE_7S_REPORT.md")
    summary = execution.model_dump(mode="json")
    (ROOT / "data" / "rescue_runs" / "latest.json").write_text(
        json.dumps({"run_directory": str(artifacts.run_directory), "execution": summary}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[10/10] Artifacts: {artifacts.run_directory}")
    if execution.state.value != "COMPLETED":
        print("FAILED:", "; ".join(execution.errors))
        return 1
    print("PASS: AUTO -> GUIDED -> AUTO -> RTL -> LAND/DISARM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
