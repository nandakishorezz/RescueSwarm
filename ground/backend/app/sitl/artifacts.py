"""Durable, local Stage 3 evidence artifacts for each actual SITL attempt."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.events import MissionEvent
from ..domain.execution import MissionExecution
from ..planning.models import CoveragePlan, PathKind
from .mission import MissionDefinition
from .protocol import MissionVerificationResult


class Stage3ArtifactWriter:
    def __init__(self, data_root: Path, mission_id: str):
        stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
        self.run_directory = data_root / "runs" / f"{stamp}_{mission_id[:8]}"
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.telemetry_path = self.run_directory / "telemetry.ndjson"
        self.events_path = self.run_directory / "events.ndjson"

    def write_plan(self, plan: CoveragePlan) -> None:
        self._json("coverage_plan.json", plan.model_dump(mode="json"))
        features = [
            {
                "type": "Feature",
                "properties": {"kind": segment.kind.value, "segment_id": segment.segment_id},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [point.longitude, point.latitude, point.altitude_m]
                        for point in segment.points_wgs84
                    ],
                },
            }
            for segment in plan.segments
        ]
        self._json("coverage_plan.geojson", {"type": "FeatureCollection", "features": features})

    def write_mission(self, mission: MissionDefinition) -> None:
        self._json("mavlink_mission.json", mission.model_dump(mode="json"))

    def write_verification(self, result: MissionVerificationResult) -> None:
        self._json("mission_verification.json", result.__dict__)

    def append_telemetry(self, payload: dict[str, Any]) -> None:
        self._append(self.telemetry_path, payload)

    def append_event(self, event: MissionEvent) -> None:
        self._append(self.events_path, event.model_dump(mode="json"))

    def finish(self, execution: MissionExecution, plan: CoveragePlan) -> None:
        self._json("execution_summary.json", execution.model_dump(mode="json"))
        self._json(
            "actual_trail.geojson",
            {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"source": execution.source.value},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[p.longitude, p.latitude, p.altitude_m] for p in execution.actual_trail],
                    },
                }],
            },
        )
        status = "PASS" if execution.state.value == "COMPLETED" else "FAIL"
        duration_s = (
            (execution.completed_at - execution.started_at).total_seconds()
            if execution.started_at and execution.completed_at else None
        )
        report = (
            "# RescueSwarm Stage 3 run\n\n"
            f"- Result: {status}\n"
            f"- Source: {execution.source.value}\n"
            f"- Planner: {plan.planner_version}\n"
            f"- Boundary area: {plan.metadata.get('boundary_area_m2', 'UNKNOWN')} m²\n"
            f"- Safe area: {plan.metadata.get('safe_area_m2', 0):.2f} m²\n"
            f"- Sweep: {plan.sweep_angle_deg:.2f}°\n"
            f"- Planned path: {plan.estimated_path_length_m:.2f} m\n"
            f"- Planned coverage: {plan.validation.planned_coverage_percent:.2f}%\n"
            f"- Actual path: {execution.actual_path_length_m:.2f} m\n"
            f"- Actual coverage: {execution.actual_coverage_percent:.2f}%\n"
            f"- Duration: {duration_s if duration_s is not None else 'UNKNOWN'} s\n"
            f"- Mission fingerprint: {execution.mission_fingerprint or 'UNKNOWN'}\n"
            f"- ArduCopter: {execution.autopilot_version or 'UNKNOWN'}\n"
            f"- ArduPilot commit: {execution.ardupilot_git_commit or 'UNKNOWN'}\n"
            f"- RTL completed: {execution.rtl_completed}\n"
            f"- Landing detected: {execution.landing_detected}\n"
            f"- Disarm detected: {execution.disarm_detected}\n"
            f"- Errors: {execution.errors or 'None'}\n"
        )
        (self.run_directory / "stage3_report.md").write_text(report, encoding="utf-8")

    def _json(self, name: str, payload: Any) -> None:
        (self.run_directory / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
        )

    @staticmethod
    def _append(path: Path, payload: Any) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
