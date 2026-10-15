"""Benchmark software-only boundary import through plan/mission artifact readiness."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], p: float) -> float:
    return float(np.percentile(values, p))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")
    plan_times, mission_times = [], []
    for index in range(args.iterations):
        start = time.perf_counter()
        origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
        frame = LocalCoordinateFrame(origin)
        boundary = Boundary(
            vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
            source_format="stage8s_setup_benchmark", area_m2=1176, area_hectares=.1176, origin=origin,
        )
        plan = CoveragePlanner().plan(f"setup-{index}", boundary, CoverageRequest(
            search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8,
            edge_margin_m=2, search_speed_m_s=6, min_planned_coverage_percent=98,
        ))
        plan_times.append((time.perf_counter() - start) * 1000)
        ArduCopterMissionBuilder().build(plan)
        mission_times.append((time.perf_counter() - start) * 1000)
    result = {
        "scope": "DEVELOPMENT_COMPUTER_SOFTWARE_ONLY",
        "physical_setup_included": False,
        "sitl_process_start_and_readiness_included": False,
        "iterations": len(plan_times),
        "import_to_plan_ready_ms": {"p50": statistics.median(plan_times), "p95": percentile(plan_times, 95), "max": max(plan_times)},
        "import_to_mavlink_mission_ready_ms": {"p50": statistics.median(mission_times), "p95": percentile(mission_times, 95), "max": max(mission_times)},
        "import_to_live_sitl_ready": "NOT_MEASURED",
    }
    path = ROOT / "data" / "resilience_runs" / "setup_benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
