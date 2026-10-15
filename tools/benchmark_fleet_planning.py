"""Benchmark offline global planning, fleet partitioning, and mission building."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from rescue_core.vision.benchmark import percentile

ROOT = Path(__file__).resolve().parents[1]


def fixture() -> Boundary:
    origin = GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500)
    frame = LocalCoordinateFrame(origin)
    vertices = [frame.to_wgs84(EnuPoint(east, north, 0)) for east, north in (
        (0, 0), (400, 0), (400, 250), (0, 250),
    )]
    return Boundary(
        vertices=vertices,
        source_format="stage9s_10_hectare_software_benchmark",
        area_m2=100_000,
        area_hectares=10,
        origin=origin,
    )


def run(iterations: int, fleet_size: int) -> dict[str, object]:
    boundary = fixture()
    request = CoverageRequest(
        search_altitude_m=20,
        lane_spacing_m=20,
        effective_swath_m=20,
        edge_margin_m=2,
        search_speed_m_s=8,
        min_planned_coverage_percent=98,
    )
    vehicle_ids = [f"drone_{index + 1:02d}" for index in range(fleet_size)]
    plan_times: list[float] = []
    ready_times: list[float] = []
    last_plan = None
    for index in range(iterations):
        started = time.perf_counter_ns()
        global_plan = CoveragePlanner().plan(f"fleet-benchmark-{index}", boundary, request)
        planned = time.perf_counter_ns()
        fleet_plan = FleetCoveragePartitioner().partition(global_plan, vehicle_ids)
        for vehicle_plan in fleet_plan.vehicle_plans.values():
            ArduCopterMissionBuilder().build(vehicle_plan)
        finished = time.perf_counter_ns()
        plan_times.append((planned - started) / 1e6)
        ready_times.append((finished - started) / 1e6)
        last_plan = fleet_plan
    return {
        "stage": "9S",
        "evidence_class": "DEVELOPMENT_COMPUTER_SOFTWARE_ONLY",
        "hardware_gate_h1": "PENDING",
        "generated_at": datetime.now(UTC).isoformat(),
        "iterations": iterations,
        "fleet_size": fleet_size,
        "boundary_area_hectares": 10,
        "coverage_segments": len(last_plan.segments) if last_plan else 0,
        "import_to_global_plan_ms": {
            "p50": percentile(plan_times, 50),
            "p95": percentile(plan_times, 95),
            "max": max(plan_times),
        },
        "import_to_fleet_missions_ready_ms": {
            "p50": percentile(ready_times, 50),
            "p95": percentile(ready_times, 95),
            "max": max(ready_times),
        },
        "physical_setup_five_minute_compliance": "NOT_MEASURED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--fleet-size", type=int, default=2)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "fleet_runs" / "planning_benchmark.json",
    )
    args = parser.parse_args()
    if args.iterations < 2 or args.fleet_size < 2:
        parser.error("iterations and fleet-size must both be at least 2")
    result = run(args.iterations, args.fleet_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
