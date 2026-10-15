"""Small durable snapshot/outbox primitives independent of SQL availability."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from .models import MissionRecoverySnapshot, StoragePriority


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: MissionRecoverySnapshot) -> Path:
        path = self.root / f"{snapshot.mission_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, mission_id: str) -> MissionRecoverySnapshot | None:
        path = self.root / f"{mission_id}.json"
        return MissionRecoverySnapshot.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


class OutboxRecord(BaseModel):
    event_id: str
    priority: StoragePriority
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempts: int = Field(default=0, ge=0)


class DurableEventOutbox:
    """Bounded NDJSON outbox; lower priority is rejected first under pressure."""

    def __init__(self, path: Path, max_bytes: int = 5_000_000):
        if max_bytes < 512:
            raise ValueError("Outbox limit is too small")
        self.path, self.max_bytes = path, max_bytes
        path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, record: OutboxRecord) -> bool:
        line = record.model_dump_json() + "\n"
        current = self.path.stat().st_size if self.path.exists() else 0
        if current + len(line.encode("utf-8")) > self.max_bytes:
            if record.priority in {StoragePriority.OPTIONAL, StoragePriority.DIAGNOSTIC}:
                return False
            self._compact_for_critical(len(line.encode("utf-8")))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        return True

    def pending(self) -> list[OutboxRecord]:
        if not self.path.is_file():
            return []
        records: list[OutboxRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(OutboxRecord.model_validate_json(line))
        return records

    def replay(self, sink: Callable[[dict[str, Any]], None]) -> tuple[int, int]:
        sent, remaining = 0, []
        seen: set[str] = set()
        for record in self.pending():
            if record.event_id in seen:
                continue
            seen.add(record.event_id)
            try:
                sink(record.payload)
                sent += 1
            except Exception:
                remaining.append(record.model_copy(update={"attempts": record.attempts + 1}))
        self._rewrite(remaining)
        return sent, len(remaining)

    def _compact_for_critical(self, required: int) -> None:
        records = self.pending()
        kept = [item for item in records if item.priority in {StoragePriority.CRITICAL, StoragePriority.MISSION}]
        while kept and sum(len(item.model_dump_json().encode("utf-8")) + 1 for item in kept) + required > self.max_bytes:
            first_mission = next((i for i, item in enumerate(kept) if item.priority is StoragePriority.MISSION), None)
            if first_mission is None:
                raise OSError("Critical outbox capacity exhausted")
            kept.pop(first_mission)
        self._rewrite(kept)

    def _rewrite(self, records: list[OutboxRecord]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("".join(item.model_dump_json() + "\n" for item in records), encoding="utf-8")
        temporary.replace(self.path)


class StoragePressurePolicy:
    def __init__(self, minimum_free_bytes: int = 250_000_000):
        self.minimum_free_bytes = minimum_free_bytes

    def allows(self, priority: StoragePriority, path: Path, free_bytes: int | None = None) -> bool:
        available = free_bytes if free_bytes is not None else shutil.disk_usage(path).free
        if available >= self.minimum_free_bytes:
            return True
        return priority in {StoragePriority.CRITICAL, StoragePriority.MISSION}
