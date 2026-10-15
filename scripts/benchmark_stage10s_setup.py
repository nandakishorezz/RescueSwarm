"""Measure cold software setup and optional two-vehicle SITL readiness."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.multi_launcher import MultiSITLLauncher
from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.services.planning_service import coverage_plan_fingerprint
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from rescue_core.qualification import load_competition_profile

ROOT = Path(__file__).resolve().parents[1]
RATES = {
    "GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10, "ATTITUDE_QUATERNION": 10,
    "GPS_RAW_INT": 5, "SYS_STATUS": 2, "MISSION_CURRENT": 3,
    "EXTENDED_SYS_STATE": 2, "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1,
}


def load_boundary(path: Path, limit: float):
    return BoundaryService(limit).import_boundary(path.name, path.read_bytes())


def request() -> CoverageRequest:
    return CoverageRequest(
        search_altitude_m=20, lane_spacing_m=20, effective_swath_m=20,
        edge_margin_m=2, search_speed_m_s=8, min_planned_coverage_percent=98,
    )


def stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "p50": median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(round(.95 * (len(ordered) - 1))))],
        "max": max(ordered),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--sitl-iterations", type=int, default=0)
    parser.add_argument("--ardupilot-home", default=os.getenv("ARDUPILOT_HOME", "/home/nandakishore7/ardupilot"))
    parser.add_argument("--speedup", type=int, default=4)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "qualification_runs" / "setup_benchmark.json")
    args = parser.parse_args()
    if args.iterations < 2 or args.sitl_iterations < 0:
        parser.error("software iterations must be >=2 and SITL iterations nonnegative")
    profile = load_competition_profile(ROOT / "config" / "competition_profile.yaml")
    fixture = ROOT / "data" / "fixtures" / "stage10s" / "near_10_hectare.geojson"
    plan_ready, fleet_ready, sitl_ready = [], [], []
    boundary = None
    missions = None
    for index in range(args.iterations):
        started = time.perf_counter()
        boundary = load_boundary(fixture, profile.competition.max_search_area_hectares)
        global_plan = CoveragePlanner().plan(f"stage10s-setup-{index}", boundary, request())
        global_plan.plan_fingerprint = coverage_plan_fingerprint(global_plan)
        planned = time.perf_counter()
        fleet_plan = FleetCoveragePartitioner().partition(global_plan, ["drone_01", "drone_02"])
        missions = {
            vehicle: ArduCopterMissionBuilder().build(plan)
            for vehicle, plan in fleet_plan.vehicle_plans.items()
        }
        built = time.perf_counter()
        plan_ready.append((planned - started) * 1000)
        fleet_ready.append((built - started) * 1000)
    identities = []
    if args.sitl_iterations:
        assert boundary is not None and missions is not None
        for index in range(args.sitl_iterations):
            launcher = MultiSITLLauncher(
                ardupilot_home=args.ardupilot_home, speedup=args.speedup, base_port=5760,
            )
            started = time.perf_counter()
            adapters = []
            try:
                identity = launcher.start_fleet(
                    2, boundary.origin.model_copy(update={"altitude_m": 584}), 0,
                    ROOT / "data" / "qualification_runs" / f"setup_sitl_{index}.log",
                )
                launcher.wait_endpoints(90)
                identities = launcher.verify_heartbeats(30)
                for instance in launcher.instances:
                    adapter = SITLAutopilotAdapter(instance.connection, "sitl", "simulation_only", 60, 20)
                    adapter.connect()
                    adapter.request_message_rates(RATES)
                    adapter.wait_ready(120)
                    adapter.upload_and_verify(missions[instance.vehicle_id])
                    adapters.append(adapter)
                sitl_ready.append((time.perf_counter() - started) * 1000)
            finally:
                for adapter in adapters:
                    adapter.disconnect()
                launcher.stop()
                time.sleep(2)
    result = {
        "stage": "10S", "generated_at": datetime.now(UTC).isoformat(),
        "hardware_gate_h1": "PENDING", "evidence_class": "SOFTWARE_SITL_SETUP_ONLY",
        "iterations": args.iterations, "sitl_iterations": args.sitl_iterations,
        "import_to_plan_ready_ms": stats(plan_ready),
        "import_to_fleet_missions_ready_ms": stats(fleet_ready),
        "import_to_sitl_ready_ms": stats(sitl_ready),
        "sitl_identities": identities,
        "physical_five_minute_setup_compliance": "NOT_MEASURED",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
