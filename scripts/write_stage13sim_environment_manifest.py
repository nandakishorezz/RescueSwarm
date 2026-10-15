"""Write the project-level Stage 13SIM environment evidence manifest."""

from __future__ import annotations

import json
from pathlib import Path

from rescue_core.simulation.gazebo import inspect_simulator_environment


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    destination = ROOT / "data" / "simulation" / "environment_manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = inspect_simulator_environment().model_dump(mode="json")
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
