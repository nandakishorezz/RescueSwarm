"""Stage 13SIM run artifact writer with segregated truth paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class SimulationArtifactWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, value: BaseModel | dict[str, Any] | list[Any]) -> Path:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        path = self.root / name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def append_jsonl(self, name: str, value: BaseModel | dict[str, Any]) -> Path:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        path = self.root / name
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
        return path

    def write_text(self, name: str, value: str) -> Path:
        path = self.root / name
        path.write_text(value, encoding="utf-8")
        return path
