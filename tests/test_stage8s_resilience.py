from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from ground.backend.app.sitl.guard import SITLSafetyError
from ground.backend.app.sitl.manager import SITLManager
from ground.backend.app.sitl.runner import SITLMissionRunner
from ground.backend.app.sitl.scenario_parameters import SITLScenarioParameterSet
from rescue_core.resilience.failures import FailureInjector, TransportFaultProfile
from rescue_core.resilience.invariants import MissionInvariantChecker
from rescue_core.resilience.models import (
    ComponentHealth, DeliveryAttemptState, FailureType, HealthState,
    MissionRecoverySnapshot, OperatorAction, RecoveryDisposition, SafetyAction,
    StoragePriority,
)
from rescue_core.resilience.persistence import (
    DurableEventOutbox, OutboxRecord, SnapshotStore, StoragePressurePolicy,
)
from rescue_core.resilience.policies import (
    BoundarySafetyMonitor, CompetitionReadinessValidator, DeliveryAttemptLedger,
    DetectionFreshnessPolicy, MissionDeadlinePolicy, OperatorActionPolicy,
)
from rescue_core.resilience.recovery import RecoveryManager
from rescue_core.resilience.safety import SafetySupervisor
from rescue_core.resilience.scenarios import ResilienceScenarioRunner, load_scenarios


SCENARIOS = Path(__file__).parents[1] / "config" / "resilience_scenarios.yaml"


def healthy(phase: str = "AUTO_SEARCH"):
    return ResilienceScenarioRunner.healthy_snapshot(phase)


def snapshot(**updates):
    base = MissionRecoverySnapshot(
        mission_id="m1", mission_fingerprint="abc", execution_source="SITL",
        orchestrator_state="SEARCHING", vehicle_id="drone_01",
        current_mission_sequence=7, coverage_percent=40,
        last_confirmed_vehicle_mode="AUTO",
    )
    return base.model_copy(update=updates)


def test_safety_supervisor_ready_and_freshness_expiry():
    health = healthy()
    assert SafetySupervisor().evaluate(health).recommended_action is SafetyAction.CONTINUE
    assert SafetySupervisor.aggregate(health) == "READY"
    health.components["gps"].last_update = health.timestamp - timedelta(seconds=10)
    assert health.components["gps"].effective_state(health.timestamp) is HealthState.STALE
    assert SafetySupervisor.aggregate(health) == "NOT_READY"


@pytest.mark.parametrize("mode", ["RTL", "LAND", "SMART_RTL"])
def test_native_failsafe_is_authoritative(mode):
    health = healthy("ALIGNMENT").model_copy(update={"vehicle_mode": mode, "previous_vehicle_mode": "GUIDED"})
    decision = SafetySupervisor().evaluate(health)
    assert decision.authoritative_layer == "AUTOPILOT"
    assert decision.recommended_action is SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE


@pytest.mark.parametrize("failure,action", [
    (FailureType.CRITICAL_BATTERY, SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE),
    (FailureType.EKF_DEGRADED, SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE),
    (FailureType.GEOFENCE_BREACH, SafetyAction.OBSERVE_AUTOPILOT_FAILSAFE),
    (FailureType.VISION_PROCESS_CRASH, SafetyAction.ABORT_TARGET_SERVICE),
    (FailureType.CAMERA_FREEZE, SafetyAction.ABORT_TARGET_SERVICE),
    (FailureType.DETECTOR_FREEZE, SafetyAction.ABORT_TARGET_SERVICE),
])
def test_target_service_failures_close_drop_gate(failure, action):
    injector = FailureInjector(engineering_enabled=True, competition_enabled=False)
    health = injector.apply(healthy("ALIGNMENT"), injector.inject(failure, "ALIGNMENT"))
    assert SafetySupervisor().evaluate(health).recommended_action is action


def test_guided_watchdog_requests_rtl():
    health = healthy("ALIGNMENT").model_copy(update={"guided_command_age_s": 2.5})
    decision = SafetySupervisor().evaluate(health)
    assert decision.recommended_action is SafetyAction.REQUEST_RTL
    assert decision.evidence["drop_permitted"] is False


def test_recovery_actual_vehicle_state_is_authoritative():
    manager = RecoveryManager()
    assert manager.reconcile(snapshot(), actual_mode="RTL", actual_armed=True, link_fresh=True, mission_sequence=8).disposition is RecoveryDisposition.OBSERVE_RETURNING
    assert manager.reconcile(snapshot(orchestrator_state="RETURNING"), actual_mode="AUTO", actual_armed=True, link_fresh=True, mission_sequence=9).disposition is RecoveryDisposition.RECONSTRUCT_AUTO
    guided = manager.reconcile(snapshot(orchestrator_state="PRECISION_ALIGNING"), actual_mode="GUIDED", actual_armed=True, link_fresh=True, mission_sequence=8)
    assert guided.disposition is RecoveryDisposition.REQUEST_RTL and not guided.may_resume_delivery
    assert manager.reconcile(snapshot(), actual_mode="AUTO", actual_armed=True, link_fresh=False, mission_sequence=8).disposition is RecoveryDisposition.FAILED_SAFE
    assert manager.reconcile(snapshot(execution_source="REAL"), actual_mode="AUTO", actual_armed=True, link_fresh=True, mission_sequence=8).disposition is RecoveryDisposition.FAILED_SAFE


def test_recovery_snapshot_round_trip(tmp_path):
    store = SnapshotStore(tmp_path)
    expected = snapshot(delivered_target_ids=["target-1"], search_checkpoint={"sequence": 7})
    store.save(expected)
    actual = store.load("m1")
    assert actual and actual.delivered_target_ids == ["target-1"]
    assert actual.search_checkpoint == {"sequence": 7}


def test_durable_outbox_is_bounded_prioritized_and_idempotent(tmp_path):
    outbox = DurableEventOutbox(tmp_path / "events.ndjson", max_bytes=900)
    assert outbox.enqueue(OutboxRecord(event_id="optional", priority=StoragePriority.OPTIONAL, payload={"data": "x" * 500}))
    assert outbox.enqueue(OutboxRecord(event_id="critical", priority=StoragePriority.CRITICAL, payload={"state": "DELIVERED"}))
    outbox.enqueue(OutboxRecord(event_id="critical", priority=StoragePriority.CRITICAL, payload={"state": "DELIVERED"}))
    received = []
    sent, remaining = outbox.replay(received.append)
    assert sent == 2 and remaining == 0
    assert [item["state"] for item in received if "state" in item] == ["DELIVERED"]


def test_storage_pressure_drops_only_low_priority(tmp_path):
    policy = StoragePressurePolicy(1000)
    assert policy.allows(StoragePriority.CRITICAL, tmp_path, free_bytes=1)
    assert policy.allows(StoragePriority.MISSION, tmp_path, free_bytes=1)
    assert not policy.allows(StoragePriority.OPTIONAL, tmp_path, free_bytes=1)


def test_delivery_attempt_exactly_once_across_restart():
    ledger = DeliveryAttemptLedger()
    attempt = ledger.begin("target-1", "slot-1")
    assert ledger.begin("target-1", "slot-1").attempt_id == attempt.attempt_id
    ledger.transition(attempt.attempt_id, DeliveryAttemptState.RELEASE_REQUESTED, "command-1")
    ledger.transition(attempt.attempt_id, DeliveryAttemptState.RELEASED)
    restored = DeliveryAttemptLedger([attempt.model_copy(deep=True)])
    assert restored.begin("target-1", "slot-1").state is DeliveryAttemptState.RELEASED
    with pytest.raises(ValueError):
        restored.transition(attempt.attempt_id, DeliveryAttemptState.RELEASE_REQUESTED, "command-2")
    restored.transition(attempt.attempt_id, DeliveryAttemptState.VERIFIED)
    assert restored.delivered_targets() == {"target-1"}


def test_delivery_verification_cannot_arrive_before_release():
    ledger = DeliveryAttemptLedger()
    attempt = ledger.begin("target", "slot")
    with pytest.raises(ValueError):
        ledger.transition(attempt.attempt_id, DeliveryAttemptState.VERIFIED)


def test_mission_invariants_pass_and_reject_contradictions():
    valid = {
        "target_state": "DELIVERED", "delivery_verification_succeeded": True,
        "rescue_state": "SEARCHING", "vehicle_mode": "AUTO", "payload_remaining": 0,
        "payload_slots": [{"target_id": "target-1"}], "delivery_attempts": [],
        "target_geolocation_valid": True, "command_connection": "tcp:127.0.0.1:5760",
    }
    assert all(item.passed for item in MissionInvariantChecker().require(valid))
    invalid = {**valid, "delivery_verification_succeeded": False, "rescue_state": "GUIDED_APPROACH", "vehicle_mode": "RTL", "autonomy_mode_override_requested": True, "target_geolocation_valid": False, "payload_remaining": -1, "command_connection": "COM3"}
    with pytest.raises(RuntimeError, match="MISSION_INVARIANT_VIOLATION"):
        MissionInvariantChecker().require(invalid)


def test_scenario_matrix_is_unique_complete_and_all_normalized_cases_pass():
    scenarios = load_scenarios(SCENARIOS)
    required = {"mavlink_loss_guided", "critical_battery_guided", "ekf_failure_guided", "geofence_breach", "emergency_recall_guided", "payload_verification_failure", "orchestrator_restart_auto"}
    assert required <= {item.scenario_id for item in scenarios}
    results = [ResilienceScenarioRunner().run(item) for item in scenarios]
    assert all(item.passed for item in results)
    assert all(item.drop_prevented for item in results if item.critical)


def test_failure_injection_is_disabled_in_competition():
    injector = FailureInjector(engineering_enabled=True, competition_enabled=True)
    with pytest.raises(PermissionError):
        injector.inject(FailureType.CAMERA_FREEZE, "ALIGNMENT")


def test_transport_impairment_is_seeded_and_bounded():
    profile = TransportFaultProfile(latency_ms=250, jitter_ms=25, packet_drop_probability=.3, seed=19)
    assert profile.outcomes(30) == profile.outcomes(30)
    assert all(225 <= latency <= 275 for _, latency in profile.outcomes(30))
    assert any(not delivered for delivered, _ in profile.outcomes(30))


def test_stale_detection_is_rejected():
    now = datetime.now(UTC)
    policy = DetectionFreshnessPolicy(.5)
    assert policy.accepts(now - timedelta(milliseconds=100), now)[0]
    assert not policy.accepts(now - timedelta(seconds=2), now)[0]


def test_operator_policy_exposes_only_three_controls():
    policy = OperatorActionPolicy()
    for action in (OperatorAction.MISSION_START, OperatorAction.SAFETY_ABORT, OperatorAction.EMERGENCY_RECALL):
        assert policy.evaluate("m", action, "READY", competition_enabled=True).allowed
    for action in (OperatorAction.MANUAL_WAYPOINT, OperatorAction.MANUAL_GOTO, OperatorAction.MANUAL_TARGET_CONFIRM, OperatorAction.MANUAL_PAYLOAD_DROP, OperatorAction.MANUAL_FLIGHT_MODE):
        assert not policy.evaluate("m", action, "RUNNING", competition_enabled=True).allowed


def test_competition_readiness_remains_not_ready_and_h1_pending():
    validator = CompetitionReadinessValidator()
    checks = validator.evaluate(offline_pass=True)
    assert validator.overall(checks) == "NOT_COMPETITION_READY"
    by_category = {item.category: item for item in checks}
    assert by_category["HARDWARE"].status == "PENDING"
    assert by_category["FLEET"].status == "FAIL"


def test_deadline_and_boundary_policies():
    deadline = MissionDeadlinePolicy(1800, 120, 180)
    assert deadline.may_start_service(100, 60)[0]
    assert not deadline.may_start_service(1600, 60)[0]
    assert deadline.active_service_action(1700) == "ABORT_TARGET_SERVICE"
    boundary = BoundarySafetyMonitor(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 2)
    assert boundary.status(5, 5) == "SAFE"
    assert boundary.status(.5, 5) == "MARGIN_LOW"
    assert boundary.status(-1, 5) == "BREACH"


class FakeParameterAdapter:
    def __init__(self, connection: str):
        self.connection_string = connection
        self.values = {"FS_GCS_ENABLE": 0.0}

    def parameter_value(self, name): return self.values.get(name)
    def set_parameter(self, name, value): self.values[name] = value


def test_disposable_sitl_parameter_set_restores_and_rejects_hardware():
    adapter = FakeParameterAdapter("tcp:127.0.0.1:5760")
    parameters = SITLScenarioParameterSet(adapter)
    evidence = parameters.apply("FS_GCS_ENABLE", 1)
    assert adapter.values["FS_GCS_ENABLE"] == 1
    parameters.restore()
    assert adapter.values["FS_GCS_ENABLE"] == 0 and evidence.restored
    with pytest.raises(SITLSafetyError):
        SITLScenarioParameterSet(FakeParameterAdapter("/dev/ttyACM0"))


def test_competition_safety_recall_only_accepts_an_existing_sitl_runner():
    manager = SITLManager.__new__(SITLManager)
    runner = SITLMissionRunner.__new__(SITLMissionRunner)
    runner.abort_requested = False
    manager.runners = {"m": runner, "bad": object()}
    assert manager.safety_recall("m")["status"] == "rtl_requested"
    assert runner.abort_requested
    with pytest.raises(PermissionError):
        manager.safety_recall("bad")
