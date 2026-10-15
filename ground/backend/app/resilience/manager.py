"""Engineering fault injection, recovery evidence, and operator audit bridge."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rescue_core.common.hardware_gates import H1_PHYSICAL_PROTOTYPE
from rescue_core.resilience.failures import FailureInjector
from rescue_core.resilience.models import (
    ComponentHealth,
    FailureType,
    HealthState,
    MissionRecoverySnapshot,
    OperatorAction,
    SafetyDecision,
    StoragePriority,
    SystemHealthSnapshot,
)
from rescue_core.resilience.persistence import DurableEventOutbox, OutboxRecord, SnapshotStore
from rescue_core.resilience.policies import CompetitionReadinessValidator, OperatorActionPolicy
from rescue_core.resilience.recovery import RecoveryManager
from rescue_core.resilience.safety import SafetySupervisor
from rescue_core.resilience.scenarios import ResilienceScenarioRunner, load_scenarios

from ..config import GroundConfig
from ..domain.events import EventSeverity, EventType, MissionEvent
from ..repositories.contracts import EventRepository, ExecutionRepository, TargetRepository, VehicleRepository
from ..services.telemetry_service import RealtimeEnvelope
from ..services.websocket_manager import WebSocketManager


class ResilienceManager:
    def __init__(
        self, config: GroundConfig, root: Path, events: EventRepository,
        executions: ExecutionRepository, targets: TargetRepository,
        vehicles: VehicleRepository, websocket: WebSocketManager, sitl_manager: Any,
    ):
        self.config, self.root = config, root
        self.events, self.executions, self.targets, self.vehicles = events, executions, targets, vehicles
        self.websocket, self.sitl_manager = websocket, sitl_manager
        self.snapshots = SnapshotStore(root / "data" / "recovery_snapshots")
        self.outbox = DurableEventOutbox(root / "data" / "outbox" / "critical_events.ndjson")
        self.injector = FailureInjector(
            engineering_enabled=config.application.mode in {"development", "engineering"},
            competition_enabled=config.competition.enabled,
        )
        self.supervisor = SafetySupervisor()
        self.recovery = RecoveryManager()
        self.operator_policy = OperatorActionPolicy()
        self.readiness = CompetitionReadinessValidator()
        self.scenarios = {item.scenario_id: item for item in load_scenarios(root / "config" / "resilience_scenarios.yaml")}
        self.last_health: SystemHealthSnapshot | None = None
        self.last_decision: SafetyDecision | None = None
        self.last_recovery: dict[str, Any] | None = None
        self.operator_actions_path = root / "data" / "operator_actions.ndjson"

    def ensure_injection_enabled(self) -> None:
        if self.config.competition.enabled or self.config.application.mode not in {"development", "engineering"}:
            raise PermissionError("Stage 8S failure injection is disabled in competition mode")

    def inject(self, mission_id: str, failure: FailureType, phase: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.ensure_injection_enabled()
        injection = self.injector.inject(failure, phase, **parameters)
        health = self._health(mission_id, phase)
        health = self.injector.apply(health, injection)
        decision = self._scenario_decision(failure, phase, health)
        self.last_health, self.last_decision = health, decision
        event = MissionEvent(
            mission_id=mission_id, type=EventType.FAILURE_INJECTED,
            severity=EventSeverity.WARNING, simulated=True,
            message=f"Engineering fault injected: {failure}",
            metadata={"injection": injection.model_dump(mode="json"), "decision": decision.model_dump(mode="json")},
        )
        self._persist_event(event)
        return {"injection": injection.model_dump(mode="json"), "decision": decision.model_dump(mode="json")}

    def run_scenario(self, scenario_id: str) -> dict[str, Any]:
        self.ensure_injection_enabled()
        try:
            scenario = self.scenarios[scenario_id]
        except KeyError as exc:
            raise ValueError(f"Unknown resilience scenario: {scenario_id}") from exc
        result = ResilienceScenarioRunner().run(scenario)
        return result.model_dump(mode="json")

    def save_recovery_snapshot(self, mission_id: str) -> MissionRecoverySnapshot:
        execution = self.executions.get(mission_id)
        if execution is None:
            raise ValueError("Mission execution does not exist")
        metadata = execution.metadata
        snapshot = MissionRecoverySnapshot(
            mission_id=mission_id,
            mission_fingerprint=execution.mission_fingerprint or "UNKNOWN",
            execution_source=execution.source,
            orchestrator_state=str(metadata.get("rescue_state", execution.state)),
            vehicle_id="drone_01",
            current_target_id=(metadata.get("target") or {}).get("target_id"),
            search_checkpoint=metadata.get("search_resume_checkpoint"),
            payload_inventory=[{"remaining": metadata.get("payload_inventory_remaining")}],
            delivered_target_ids=[item.target_id for item in self.targets.list_for_mission(mission_id) if item.delivery_state == "DELIVERED"],
            delivery_attempts=metadata.get("delivery_attempts", []),
            current_mission_sequence=execution.current_mission_item,
            coverage_percent=execution.actual_coverage_percent,
            last_confirmed_vehicle_mode=(metadata.get("mode_history") or ["UNKNOWN"])[-1],
        )
        path = self.snapshots.save(snapshot)
        self._persist_event(MissionEvent(
            mission_id=mission_id, type=EventType.RECOVERY_SNAPSHOT_SAVED,
            message="Compact mission recovery snapshot persisted",
            metadata={"path": str(path), "snapshot_timestamp": snapshot.timestamp.isoformat()},
        ))
        return snapshot

    def reconcile(self, mission_id: str) -> dict[str, Any]:
        snapshot = self.snapshots.load(mission_id)
        if snapshot is None:
            raise ValueError("Recovery snapshot does not exist")
        vehicles = self.vehicles.list_for_mission(mission_id)
        if vehicles:
            vehicle = vehicles[0]
            age = (datetime.now(UTC) - vehicle.last_update).total_seconds()
            actual_mode, armed, fresh = vehicle.flight_mode, vehicle.armed, age <= self.config.realtime.degraded_threshold_seconds
        else:
            actual_mode, armed, fresh = "UNKNOWN", False, False
        result = self.recovery.reconcile(
            snapshot, actual_mode=actual_mode, actual_armed=armed,
            link_fresh=fresh, mission_sequence=snapshot.current_mission_sequence,
        )
        self.last_recovery = result.model_dump(mode="json")
        self._persist_event(MissionEvent(
            mission_id=mission_id, type=EventType.RECOVERY_RECONCILED,
            severity=EventSeverity.WARNING if result.disposition in {"REQUEST_RTL", "FAILED_SAFE"} else EventSeverity.INFO,
            message=result.reason, metadata=self.last_recovery,
        ))
        return self.last_recovery

    def operator_action(self, mission_id: str, action: OperatorAction, mission_state: str) -> dict[str, Any]:
        audit = self.operator_policy.evaluate(
            mission_id, action, mission_state, competition_enabled=self.config.competition.enabled,
        )
        self.operator_actions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.operator_actions_path.open("a", encoding="utf-8") as stream:
            stream.write(audit.model_dump_json() + "\n")
        self._persist_event(MissionEvent(
            mission_id=mission_id, type=EventType.OPERATOR_ACTION,
            severity=EventSeverity.INFO if audit.allowed else EventSeverity.WARNING,
            message=f"Operator action {action}: {'ALLOWED' if audit.allowed else 'REJECTED'}",
            metadata=audit.model_dump(mode="json"),
        ))
        if audit.allowed and action in {OperatorAction.SAFETY_ABORT, OperatorAction.EMERGENCY_RECALL}:
            # Both map to verified SITL RTL today, but remain distinct audited domain actions.
            self.sitl_manager.safety_recall(mission_id)
        return audit.model_dump(mode="json")

    def status(self, mission_id: str | None = None) -> dict[str, Any]:
        checks = self.readiness.evaluate(offline_pass=True)
        return {
            "hardware_gate_h1": H1_PHYSICAL_PROTOTYPE.model_dump(mode="json"),
            "health": self.last_health.model_dump(mode="json") if self.last_health else None,
            "aggregate_health": self.supervisor.aggregate(self.last_health) if self.last_health else "NOT_READY",
            "safety_decision": self.last_decision.model_dump(mode="json") if self.last_decision else None,
            "recovery": self.last_recovery,
            "competition_locked": self.config.competition.enabled,
            "failure_injection_enabled": not self.config.competition.enabled and self.config.application.mode in {"development", "engineering"},
            "competition_readiness": {
                "overall": self.readiness.overall(checks),
                "checks": [item.model_dump(mode="json") for item in checks],
            },
            "scenario_count": len(self.scenarios),
            "critical_scenario_count": sum(item.critical for item in self.scenarios.values()),
            "mission_id": mission_id,
        }

    def _health(self, mission_id: str, phase: str) -> SystemHealthSnapshot:
        return ResilienceScenarioRunner.healthy_snapshot(phase).model_copy(update={"mission_id": mission_id})

    def _scenario_decision(self, failure: FailureType, phase: str, health: SystemHealthSnapshot) -> SafetyDecision:
        matching = next((item for item in self.scenarios.values() if item.failure is failure and item.phase == phase), None)
        if matching:
            runner = ResilienceScenarioRunner()
            return runner.run(matching).decision
        return self.supervisor.evaluate(health)

    def _persist_event(self, event: MissionEvent) -> None:
        try:
            self.events.add(event)
        except Exception:
            self.outbox.enqueue(OutboxRecord(
                event_id=event.event_id, priority=StoragePriority.CRITICAL,
                payload=event.model_dump(mode="json"),
            ))
