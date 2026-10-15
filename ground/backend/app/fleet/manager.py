"""GCS-facing fleet planning/status manager; live runners remain engineering-only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import GroundConfig
from ..services.planning_service import PlanningService
from .artifacts import FleetArtifactWriter
from .partition import FleetCoveragePartitioner


class FleetManager:
    def __init__(self, config: GroundConfig, root: Path, planning: PlanningService):
        self.config, self.root, self.planning = config, root, planning
        self._plans: dict[str, Any] = {}

    def ensure_engineering_enabled(self) -> None:
        if self.config.competition.enabled:
            raise PermissionError("Fleet engineering controls are disabled in Competition Mode")
        if not self.config.fleet.enabled:
            raise PermissionError("Fleet simulation is disabled")

    def plan(self, mission_id: str, fleet_size: int | None = None) -> dict[str, Any]:
        self.ensure_engineering_enabled()
        size = fleet_size or self.config.fleet.fleet_size
        coverage = self.planning.get(mission_id)
        if coverage is None:
            raise ValueError("Generate the global Stage 3 coverage plan before fleet partitioning")
        vehicle_ids = [f"drone_{index + 1:02d}" for index in range(size)]
        fleet_plan = FleetCoveragePartitioner().partition(coverage, vehicle_ids)
        self._plans[mission_id] = fleet_plan
        state_path = self.root / "data" / "fleet_state" / f"{mission_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(fleet_plan.model_dump_json(indent=2), encoding="utf-8")
        return fleet_plan.model_dump(mode="json")

    def get_plan(self, mission_id: str) -> dict[str, Any] | None:
        if mission_id in self._plans:
            return self._plans[mission_id].model_dump(mode="json")
        path = self.root / "data" / "fleet_state" / f"{mission_id}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def status(self, mission_id: str) -> dict[str, Any]:
        plan = self.get_plan(mission_id)
        if plan is None:
            return {
                "mission_id": mission_id, "state": "NOT_PLANNED", "fleet_size": 0,
                "hardware_gate_h1": "PENDING", "execution_source": "SITL_ONLY",
            }
        latest_root = self.root / "data" / "fleet_runs"
        latest = sorted((item for item in latest_root.iterdir() if item.is_dir()), reverse=True)[0] if latest_root.is_dir() and any(latest_root.iterdir()) else None
        summary = None
        if latest and (latest / "fleet_execution_summary.json").is_file():
            candidate = json.loads((latest / "fleet_execution_summary.json").read_text(encoding="utf-8"))
            if candidate.get("mission_id") == mission_id:
                summary = candidate
        vehicles = (summary or {}).get("vehicles", [])
        if isinstance(vehicles, dict):
            vehicles = list(vehicles.values())
        return {
            "mission_id": mission_id,
            "state": (summary or {}).get("state", "PLAN_READY"),
            "fleet_size": plan["fleet_size"],
            "vehicle_ids": plan["vehicle_ids"],
            "partition": plan["metrics"],
            "assignments": plan["assignments"],
            "vehicles": vehicles,
            "targets": (summary or {}).get("targets", []),
            "progress": (summary or {}).get("progress", {}),
            "payload_inventory": (summary or {}).get("payload_inventory", {}),
            "latest_run": latest.name if latest else None,
            "hardware_gate_h1": "PENDING",
            "execution_source": "SITL_ONLY",
            "video": {vehicle: "SIMULATED" for vehicle in plan["vehicle_ids"]},
        }
