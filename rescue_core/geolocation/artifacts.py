"""Stage 5 evidence writer with calibration/pose/observation provenance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .calibration import CameraCalibration, CameraExtrinsics


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class GeolocationArtifactWriter:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)

    def write_json(self, name: str, value: Any) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(_json_value(value), indent=2), encoding="utf-8")
        return path

    def write_yaml(self, name: str, value: Any) -> Path:
        path = self.directory / name
        payload = _json_value(value)
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return path

    def write_ndjson(self, name: str, values: list[Any]) -> Path:
        path = self.directory / name
        with path.open("w", encoding="utf-8") as stream:
            for value in values:
                stream.write(json.dumps(_json_value(value), separators=(",", ":")) + "\n")
        return path

    def write_required(
        self, *, manifest: dict[str, Any], calibration: CameraCalibration,
        extrinsics: CameraExtrinsics, clock_sync: dict[str, Any], poses: list[Any],
        observations: list[Any], targets: list[Any], ground_truth: dict[str, Any] | None,
        metrics: dict[str, Any], report: str,
    ) -> None:
        self.write_json("run_manifest.json", manifest)
        self.write_yaml("camera_intrinsics.yaml", calibration)
        self.write_yaml("camera_extrinsics.yaml", extrinsics)
        self.write_json("clock_sync.json", clock_sync)
        self.write_ndjson("pose_samples.ndjson", poses)
        self.write_ndjson("observations.ndjson", observations)
        self.write_json("targets.json", targets)
        if ground_truth is not None:
            self.write_json("ground_truth.json", ground_truth)
        self.write_json("geolocation_metrics.json", metrics)
        (self.directory / "geolocation_report.md").write_text(report, encoding="utf-8")
