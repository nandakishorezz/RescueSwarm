#!/usr/bin/env python3
"""Plan and optionally execute a complete local ArduCopter SITL search mission."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ground.backend.app.config import load_ground_config  # noqa: E402
from ground.backend.app.main import create_app  # noqa: E402
from ground.backend.app.planning.models import CoverageRequest  # noqa: E402
from tools.generate_stage3_report import render  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    config = load_ground_config(args.config)
    if args.ardupilot_home:
        config.sitl.ardupilot_home = args.ardupilot_home
    if args.speedup is not None:
        config.sitl.launcher_speedup = args.speedup
    app = create_app(config)
    print("[1/10] Boundary loaded")
    content = args.boundary.read_bytes()
    boundary = app.state.boundary_service.import_boundary(args.boundary.name, content)
    mission = app.state.mission_service.create(args.name)
    app.state.mission_service.attach_boundary(mission.id, boundary)
    app.state.mission_service.validate(mission.id)
    print(f"[2/10] Boundary validated ({boundary.area_hectares:.3f} ha)")
    coverage = config.coverage
    plan = app.state.planning_service.generate(
        mission.id,
        CoverageRequest(
            search_altitude_m=coverage.search_altitude_m,
            lane_spacing_m=coverage.lane_spacing_m,
            effective_swath_m=coverage.effective_swath_m,
            edge_margin_m=coverage.edge_margin_m,
            search_speed_m_s=coverage.search_speed_m_s,
            sweep_strategy=coverage.sweep_strategy.upper(),
            fixed_sweep_angle_deg=coverage.fixed_sweep_angle_deg,
            turn_penalty_m=coverage.turn_penalty_m,
            min_planned_coverage_percent=coverage.min_planned_coverage_percent,
            max_waypoints=coverage.max_waypoints,
        ),
    )
    print(f"[3/10] Optimized plan generated ({plan.estimated_path_length_m:.1f} m, {plan.sweep_angle_deg:.1f} deg)")
    if not args.execute:
        from ground.backend.app.sitl.artifacts import Stage3ArtifactWriter
        writer = Stage3ArtifactWriter(ROOT / "data", mission.id)
        writer.write_plan(plan)
        shutil.copyfile(args.boundary, writer.run_directory / f"mission_input{args.boundary.suffix.lower()}")
        (ROOT / "STAGE_3_REPORT.md").write_text(render(writer.run_directory), encoding="utf-8")
        print("[4/10] Live SITL NOT_TESTED (add --execute, optionally --start-sitl)")
        print(f"Artifacts: {writer.run_directory}")
        return 0
    manager = app.state.sitl_manager
    started_here = False
    try:
        if args.start_sitl:
            if args.launch_altitude_m is None:
                raise ValueError("--launch-altitude-m is required with --start-sitl")
            manager.start_process(mission.id, args.launch_altitude_m)
            started_here = True
            print("[4/10] Official ArduCopter SITL started")
        else:
            print("[4/10] Using existing local ArduCopter SITL")
        await manager.execute(mission.id)
        print("[5/10] MAVLink execution task started")
        shutil.copyfile(args.boundary, manager.latest_run_directory / f"mission_input{args.boundary.suffix.lower()}")
        await manager.tasks[mission.id]
        execution = manager.executions.get(mission.id)
        if execution.state.value != "COMPLETED":
            (ROOT / "STAGE_3_REPORT.md").write_text(render(manager.latest_run_directory), encoding="utf-8")
            print(f"[6/10] Execution stopped safely: {execution.errors}", file=sys.stderr)
            print(f"Artifacts: {manager.latest_run_directory}")
            return 1
        print("[6/10] Mission upload and download verification complete")
        print("[7/10] Arm/AUTO/search monitor complete")
        print("[8/10] RTL/landing monitor complete")
        print(f"[9/10] Actual coverage: {execution.actual_coverage_percent:.2f}%")
        (ROOT / "STAGE_3_REPORT.md").write_text(render(manager.latest_run_directory), encoding="utf-8")
        print(f"[10/10] Report and artifacts written to {manager.latest_run_directory}")
        return 0
    finally:
        if started_here:
            manager.stop_process()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boundary", type=Path)
    parser.add_argument("--name", default="Stage 3 SITL Search")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "ground.yaml")
    parser.add_argument("--execute", action="store_true", help="Run against the guarded local SITL endpoint")
    parser.add_argument("--start-sitl", action="store_true", help="Launch official sim_vehicle.py before execution")
    parser.add_argument("--launch-altitude-m", type=float, help="Mission-site MSL altitude for SITL custom home")
    parser.add_argument("--ardupilot-home", help="ArduPilot source path (Linux/WSL syntax when appropriate)")
    parser.add_argument(
        "--speedup",
        type=int,
        choices=range(1, 101),
        metavar="1-100",
        help="Override the local SITL physics speedup (test/development only)",
    )
    args = parser.parse_args()
    if not args.boundary.is_file():
        parser.error(f"Boundary does not exist: {args.boundary}")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
