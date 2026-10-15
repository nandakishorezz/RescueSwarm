#!/usr/bin/env python3
"""Run the local Stage 2 ground backend from repository configuration."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import uvicorn

from ground.backend.app.config import load_ground_config


def main() -> int:
    config = load_ground_config(REPOSITORY_ROOT / "config" / "ground.yaml")
    uvicorn.run(
        "ground.backend.app.main:create_app",
        host=config.server.host,
        port=config.server.port,
        reload=False,
        factory=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
