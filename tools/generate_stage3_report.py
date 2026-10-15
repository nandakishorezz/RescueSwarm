#!/usr/bin/env python3
"""Generate an evidence-based Stage 3 report from the latest local run artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _events(run_directory: Path | None) -> list[dict[str, Any]]:
    path = run_directory / "events.ndjson" if run_directory else None
    if not path or not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _boundary_area(run_directory: Path | None) -> float | str:
    if not run_directory:
        return "NOT_TESTED"
    inputs = sorted(run_directory.glob("mission_input.*"))
    if not inputs:
        return "UNKNOWN"
    try:
        from ground.backend.app.services.boundary_service import BoundaryService

        source = inputs[0]
        return BoundaryService(max_area_hectares=1_000_000).import_boundary(
            source.name, source.read_bytes()
        ).area_m2
    except (OSError, ValueError):
        return "UNKNOWN"


def _duration(execution: dict[str, Any] | None) -> float | str:
    if not execution or not execution.get("started_at") or not execution.get("completed_at"):
        return "NOT_TESTED"
    start = datetime.fromisoformat(execution["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(execution["completed_at"].replace("Z", "+00:00"))
    return round((end - start).total_seconds(), 3)


def render(run_directory: Path | None) -> str:
    plan = _load(run_directory / "coverage_plan.json") if run_directory else None
    execution = _load(run_directory / "execution_summary.json") if run_directory else None
    verification = _load(run_directory / "mission_verification.json") if run_directory else None
    live = bool(execution)
    passed = live and execution.get("state") == "COMPLETED"
    status = "PASS" if passed else ("FAIL" if live else "NOT_TESTED")
    configuration = plan.get("configuration", {}) if plan else {}
    validation = plan.get("validation", {}) if plan else {}
    events = _events(run_directory)
    event_types = {event.get("type") for event in events}
    readiness = next((event.get("metadata", {}) for event in events if event.get("type") == "SITL_READY"), {})
    boundary_area = (
        plan.get("metadata", {}).get("boundary_area_m2") if plan else None
    ) or _boundary_area(run_directory)
    lines = [
        "# RescueSwarm Stage 3 Report", "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Live SITL execution: **{status}**", "",
        "## Planner", "",
        f"- Boundary area: {boundary_area} m²",
        f"- Safe area: {plan.get('metadata', {}).get('safe_area_m2', 'UNKNOWN') if plan else 'NOT_TESTED'} m²",
        f"- Sweep angle: {plan.get('sweep_angle_deg', 'NOT_TESTED') if plan else 'NOT_TESTED'}°",
        f"- Lane spacing: {configuration.get('lane_spacing_m', 'NOT_TESTED')} m",
        f"- Search altitude: {configuration.get('search_altitude_m', 'NOT_TESTED')} m",
        f"- Search speed: {configuration.get('search_speed_m_s', 'NOT_TESTED')} m/s",
        f"- Path length: {plan.get('estimated_path_length_m', 'NOT_TESTED') if plan else 'NOT_TESTED'} m",
        f"- Waypoints: {len(plan.get('path_points_wgs84', [])) if plan else 'NOT_TESTED'}",
        f"- Planned coverage: {validation.get('planned_coverage_percent', 'NOT_TESTED')}%", "",
        "## SITL", "",
        f"- ArduPilot commit: {execution.get('ardupilot_git_commit', 'UNKNOWN') if execution else 'NOT_TESTED'}",
        f"- ArduCopter version: {execution.get('autopilot_version', 'UNKNOWN') if execution else 'NOT_TESTED'}",
        f"- Connection: {execution.get('connection', 'NOT_TESTED') if execution else 'NOT_TESTED'}",
        f"- GPS readiness: {'PASS' if readiness.get('gps_fix_type', 0) >= 3 else ('FAIL' if live else 'NOT_TESTED')}",
        f"- GPS fix/satellites: {readiness.get('gps_fix_type', 'UNKNOWN')} / {readiness.get('satellites', 'UNKNOWN')}",
        f"- EKF/armability readiness: {'PASS' if 'SITL_READY' in event_types else ('FAIL' if live else 'NOT_TESTED')}", "",
        "## Mission protocol", "",
        f"- Upload: {'PASS' if verification else 'NOT_TESTED'}",
        f"- Download: {'PASS' if verification else 'NOT_TESTED'}",
        f"- Verification: {'PASS' if verification and verification.get('valid') else ('FAIL' if verification else 'NOT_TESTED')}", "",
        "## Execution", "",
    ]
    for label, key in [
        ("Arm", "mission_fingerprint"), ("AUTO", "started_at"), ("Takeoff", "started_at"),
        ("Waypoints reached", "waypoints_reached"), ("RTL", "rtl_completed"),
        ("Landing", "landing_detected"), ("Disarm", "disarm_detected"),
    ]:
        value = execution.get(key) if execution else None
        result = "PASS" if value else ("FAIL" if execution else "NOT_TESTED")
        lines.append(f"- {label}: {result}")
    lines.extend([
        "", "## Actual results", "",
        f"- Duration: {_duration(execution)} s",
        f"- Mission fingerprint: {execution.get('mission_fingerprint', 'NOT_TESTED') if execution else 'NOT_TESTED'}",
        f"- Mission items reached: {len(execution.get('waypoints_reached', [])) if execution else 'NOT_TESTED'} / {execution.get('total_mission_items', 'NOT_TESTED') if execution else 'NOT_TESTED'}",
        f"- Search waypoints completed: {len([item for item in execution.get('waypoints_reached', []) if 3 <= item < execution.get('total_mission_items', 1) - 1]) if execution else 'NOT_TESTED'} / {len(plan.get('path_points_wgs84', [])) if plan else 'NOT_TESTED'}",
        f"- Actual distance: {execution.get('actual_path_length_m', 'NOT_TESTED') if execution else 'NOT_TESTED'} m",
        f"- Actual coverage: {execution.get('actual_coverage_percent', 'NOT_TESTED') if execution else 'NOT_TESTED'}%",
        f"- Missed area: {execution.get('missed_area_m2', 'NOT_TESTED') if execution else 'NOT_TESTED'} m²",
        f"- Revisited/overlap area estimate: {execution.get('overlap_area_m2', 'NOT_TESTED') if execution else 'NOT_TESTED'} m²",
        f"- Mean/P95/max path deviation: {execution.get('mean_deviation_m', 'NOT_TESTED') if execution else 'NOT_TESTED'} / {execution.get('p95_deviation_m', 'NOT_TESTED') if execution else 'NOT_TESTED'} / {execution.get('max_deviation_m', 'NOT_TESTED') if execution else 'NOT_TESTED'} m",
        f"- Warnings/errors: {execution.get('errors', []) if execution else 'NOT_TESTED'}", "",
        f"Artifacts: `{run_directory}`" if run_directory else "Artifacts: none (live SITL not run).", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "STAGE_3_REPORT.md")
    args = parser.parse_args()
    run_directory = args.run_directory
    if run_directory is None:
        candidates = sorted((ROOT / "data" / "runs").glob("*/coverage_plan.json"))
        run_directory = candidates[-1].parent if candidates else None
    args.output.write_text(render(run_directory), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
