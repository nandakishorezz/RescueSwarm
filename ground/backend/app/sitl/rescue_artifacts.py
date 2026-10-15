"""Auditable local artifacts for Stage 7S SITL rescue executions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class Stage7SArtifactWriter:
    def __init__(self, data_root: Path, mission_id: str):
        run_id = f"{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}_{uuid4().hex[:8]}"
        self.run_directory = data_root / "rescue_runs" / f"{run_id}_{mission_id}"
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.events_path = self.run_directory / "events.ndjson"
        self.telemetry_path = self.run_directory / "telemetry.ndjson"
        self.observations_path = self.run_directory / "target_observations.ndjson"

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    def write_json(self, name: str, value: Any) -> None:
        (self.run_directory / name).write_text(
            json.dumps(self._json(value), indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

    def append(self, path: Path, value: Any) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self._json(value), sort_keys=True, default=str) + "\n")

    def append_event(self, value: Any) -> None:
        self.append(self.events_path, value)

    def append_telemetry(self, value: Any) -> None:
        self.append(self.telemetry_path, value)

    def append_observation(self, value: Any) -> None:
        self.append(self.observations_path, value)

    def write_geojson(self, name: str, coordinates: list[tuple[float, float]], properties: dict[str, Any]) -> None:
        self.write_json(name, {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": properties,
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }],
        })

    def finish(self, summary: dict[str, Any]) -> None:
        self.write_json("execution_summary.json", summary)
        report = summary.get("report_markdown")
        if report:
            (self.run_directory / "stage7s_report.md").write_text(str(report), encoding="utf-8")
