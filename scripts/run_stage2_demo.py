#!/usr/bin/env python3
"""Run a deterministic Stage 2 GCS simulation without any real drone connection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ground.backend.app.config import load_ground_config
from ground.backend.app.main import create_app


async def run(database_url: str) -> dict[str, object]:
    config = load_ground_config(REPOSITORY_ROOT / "config" / "ground.yaml")
    config = config.model_copy(
        update={"database": config.database.model_copy(update={"url": database_url})}
    )
    app = create_app(config)
    service = app.state.mission_service
    mission = service.create("Stage 2 deterministic GCS demonstration")
    content = (REPOSITORY_ROOT / "data" / "examples" / "sample_boundary.geojson").read_bytes()
    boundary = app.state.boundary_service.import_boundary("sample_boundary.geojson", content)
    service.attach_boundary(mission.id, boundary)
    service.validate(mission.id)
    await app.state.simulator_manager.complete(mission.id)
    review = service.review(mission.id)
    await app.state.simulator_manager.shutdown()
    return {
        "simulation": "GCS EVENT SIMULATOR — NO REAL AIRCRAFT",
        "mission_id": mission.id,
        "final_state": review["mission"].state,
        "coverage_percent": review["mission"].coverage_percent,
        "targets_found": review["targets_found"],
        "targets_confirmed": review["targets_confirmed"],
        "deliveries_completed": review["deliveries_completed"],
        "event_count": len(review["events"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="sqlite:///data/ground/stage2_demo.db")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.database_url)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

