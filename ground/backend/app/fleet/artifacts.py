"""Repeatable Stage 9S run evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class FleetArtifactWriter:
    REQUIRED = (
        "fleet_plan.json", "partition.geojson", "vehicle_registry.json",
        "separation_metrics.json", "fleet_metrics.json", "fleet_execution_summary.json",
    )

    def __init__(self, data_root: Path, run_id: str | None = None):
        run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_stage9s"
        self.run_directory = data_root / "fleet_runs" / run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)

    def write_json(self, filename: str, value: Any) -> Path:
        path = self.run_directory / filename
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    def append(self, filename: str, value: Any) -> None:
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        with (self.run_directory / filename).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, default=str) + "\n")

    def missing_required(self) -> list[str]:
        return [name for name in self.REQUIRED if not (self.run_directory / name).exists()]
