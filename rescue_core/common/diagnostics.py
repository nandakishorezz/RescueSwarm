"""Filesystem and subprocess helpers shared by diagnostic tools."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def timestamp_slug() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")


def diagnostic_directory(base: str | Path = "data/diagnostics") -> Path:
    path = Path(base) / timestamp_slug()
    path.mkdir(parents=True, exist_ok=False)
    return path


def ensure_output_directory(path: str | Path | None) -> Path:
    if path is None:
        return diagnostic_directory()
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    return output


def write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def run_command(command: list[str], timeout: float = 15.0) -> dict[str, Any]:
    """Run a read-only diagnostic command, preserving failures as evidence."""

    executable = shutil.which(command[0])
    if executable is None:
        return {"command": command, "status": "not_available", "returncode": None}
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "timeout",
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    return {
        "command": command,
        "status": "completed",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def read_text_if_present(path: str | Path) -> str | None:
    candidate = Path(path)
    try:
        return candidate.read_text(encoding="utf-8", errors="replace") if candidate.is_file() else None
    except PermissionError:
        return None

