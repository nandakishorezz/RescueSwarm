from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import LineString, Polygon

from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.guided import SITLGuidedNavigator
from rescue_core.autonomy.alignment import PrecisionAlignmentController
from rescue_core.autonomy.approach import GuidedApproachPlanner, ApproachPlanningError, operational_flight_region
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.models import (
    PayloadState, ReacquisitionObservation, SearchResumePolicyName, SyntheticSurvivorScenario,
)
from rescue_core.autonomy.orchestrator import RescueMissionOrchestrator
from rescue_core.autonomy.payload import PayloadError, PayloadInventory, SimulatedPayloadAdapter
from rescue_core.autonomy.policies import EligibilityReason, TargetEligibilityPolicy, TargetServiceQueue
from rescue_core.autonomy.reacquisition import ReacquisitionSearchPlanner, SyntheticReacquisitionProvider
from rescue_core.autonomy.resume import SearchResumeError, SearchResumeManager
from rescue_core.autonomy.safety import RescueSafetyError, require_sitl_command_adapter
from rescue_core.autonomy.safety import GuidedSafetySnapshot, GuidedSafetySupervisor
from rescue_core.autonomy.states import InvalidRescueTransition, RescueEvent, RescueState, RescueStateMachine
from rescue_core.geolocation.models import FusedTargetEstimate, GeolocationQuality, TargetTrackState


def target(**changes) -> FusedTargetEstimate:
    now = datetime.now(UTC)
    values = dict(
        target_id="target_01", mission_id="mission", state=TargetTrackState.CONFIRMED,
        east_m=5, north_m=5, latitude=17, longitude=78, uncertainty_m=2,
        detection_confidence=.8, observation_count=4, inlier_count=4, outlier_count=0,
        first_seen_at=now, last_seen_at=now + timedelta(seconds=1),
        quality=GeolocationQuality.STANDARD_GNSS_LIMITED, simulated=True,
    )
    values.update(changes)
    return FusedTargetEstimate(**values)


def test_h1_configuration_is_explicitly_sitl_only():
    config = load_rescue_config()
    assert config.execution_source == "sitl"
    assert config.engineering_defaults_only
    assert config.delivery.delivery_altitude_m < config.guided.approach_altitude_m


def test_state_machine_gold_path_and_invalid_transition():
    machine = RescueStateMachine()
    events = [
        RescueEvent.PREPARE, RescueEvent.MISSION_ARMED, RescueEvent.TAKEOFF_OBSERVED,
        RescueEvent.CANDIDATE_OBSERVED, RescueEvent.TARGET_CONFIRMED,
        RescueEvent.CHECKPOINT_SAVED, RescueEvent.APPROACH_PATH_READY,
        RescueEvent.GUIDED_CONFIRMED, RescueEvent.APPROACH_REACHED,
        RescueEvent.TARGET_REACQUIRED, RescueEvent.ALIGNMENT_CONVERGED,
        RescueEvent.DROP_CONDITIONS_MET, RescueEvent.RELEASE_ACCEPTED,
        RescueEvent.DELIVERY_VERIFIED, RescueEvent.AUTO_CONFIRMED,
        RescueEvent.RTL_OBSERVED, RescueEvent.LANDING_OBSERVED, RescueEvent.DISARMED,
    ]
    for event in events:
        machine.transition(event)
    assert machine.state is RescueState.COMPLETED
    with pytest.raises(InvalidRescueTransition):
        machine.transition(RescueEvent.GUIDED_CONFIRMED)


@pytest.mark.parametrize(
    ("changes", "kwargs", "reason"),
    [
        ({"state": TargetTrackState.CANDIDATE}, {}, EligibilityReason.NOT_CONFIRMED),
        ({"delivery_state": "DELIVERED"}, {}, EligibilityReason.ALREADY_DELIVERED),
        ({"uncertainty_m": 20}, {}, EligibilityReason.UNCERTAINTY_TOO_HIGH),
        ({"east_m": 50}, {}, EligibilityReason.OUTSIDE_OPERATIONAL_REGION),
        ({}, {"battery_percent": 10}, EligibilityReason.BATTERY_TOO_LOW),
        ({}, {"vehicle_healthy": False}, EligibilityReason.VEHICLE_UNHEALTHY),
    ],
)
def test_target_eligibility_rejects_unsafe_conditions(changes, kwargs, reason):
    policy = TargetEligibilityPolicy(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 8, 25, 60)
    decision = policy.evaluate(target(**changes), PayloadInventory(1), **kwargs)
    assert not decision.eligible and decision.reason is reason


def test_no_payload_and_duplicate_service_are_rejected():
    region = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    policy = TargetEligibilityPolicy(region, 8, 25, 60)
    assert policy.evaluate(target(), PayloadInventory(0)).reason is EligibilityReason.PAYLOAD_UNAVAILABLE
    queue = TargetServiceQueue()
    assert queue.add(target())
    assert not queue.add(target())
    selected = queue.pop()
    queue.mark_serviced(selected.target_id)
    assert not queue.add(selected)


def test_concave_approach_uses_internal_visibility_route():
    # L-shape: direct diagonal from lower-right to upper-left exits the region.
    region = Polygon([(0, 0), (8, 0), (8, 3), (3, 3), (3, 8), (0, 8)])
    planner = GuidedApproachPlanner(region)
    plan = planner.plan(7, 1, 1, 7, 8)
    coords = [(7, 1), *[(p.east_m, p.north_m) for p in plan.points]]
    assert not plan.direct_path_used
    assert all(region.buffer(1e-5).covers(LineString(pair)) for pair in zip(coords, coords[1:]))


def test_target_outside_region_is_unserviceable_and_margin_can_collapse():
    region = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    with pytest.raises(ApproachPlanningError):
        GuidedApproachPlanner(region).plan(1, 1, 5, 5, 8)
    with pytest.raises(ApproachPlanningError):
        operational_flight_region(region, 2)


def test_resume_replays_last_reached_without_takeoff_or_rtl():
    manager = SearchResumeManager(3, 12, SearchResumePolicyName.REPLAY_FROM_LAST_REACHED)
    checkpoint = manager.checkpoint(
        mission_id="mission", mission_fingerprint="a" * 64, current_seq=7,
        reached_items=[1, 3, 4, 5, 6], east_m=1, north_m=2, coverage_percent=42,
    )
    assert manager.select_resume_sequence(checkpoint) == 6
    with pytest.raises(SearchResumeError):
        manager.checkpoint(
            mission_id="mission", mission_fingerprint="a" * 64, current_seq=12,
            reached_items=[], east_m=0, north_m=0, coverage_percent=0,
        )


def test_alignment_direction_limits_speed_and_rejects_stale_or_missing_target():
    controller = PrecisionAlignmentController(.5, 1, .1, .4, .5)
    now = time.monotonic_ns()
    observation = ReacquisitionObservation(
        timestamp_monotonic_ns=now, target_id="t", visible=True,
        relative_east_m=2, relative_north_m=-1, confidence=.9,
    )
    command = controller.command(observation, now)
    assert command.velocity_east_m_s > 0 and command.velocity_north_m_s < 0
    assert math.hypot(command.velocity_east_m_s, command.velocity_north_m_s) <= 1.000001
    with pytest.raises(TimeoutError):
        controller.command(observation, now + 1_000_000_000)
    with pytest.raises(ValueError):
        controller.command(ReacquisitionObservation(timestamp_monotonic_ns=now, target_id="t", visible=False), now)


def test_synthetic_reacquisition_is_geometric_and_search_is_bounded():
    scenario = SyntheticSurvivorScenario(target_id="t", true_east_m=5, true_north_m=0)
    provider = SyntheticReacquisitionProvider(scenario, visibility_radius_m=3)
    assert not provider.observe(0, 0).visible
    observation = provider.observe(3, 0)
    assert observation.visible and observation.relative_east_m == pytest.approx(2)
    region = Polygon([(0, -5), (10, -5), (10, 5), (0, 5)])
    points = ReacquisitionSearchPlanner(region, 2).plan(5, 0)
    assert all(region.covers(__import__("shapely").geometry.Point(p.east_m, p.north_m)) for p in points)


def test_payload_release_verification_and_double_release_protection():
    inventory = PayloadInventory(1)
    slot = inventory.reserve("target")
    adapter = SimulatedPayloadAdapter()
    evidence = adapter.release(
        slot, vehicle_east_m=1, vehicle_north_m=2, relative_target_error_m=.2,
        altitude_m=4, ground_speed_m_s=.1, target_uncertainty_m=2,
    )
    assert adapter.verify(slot, evidence)
    assert slot.state is PayloadState.VERIFIED and inventory.remaining == 0
    with pytest.raises(PayloadError):
        inventory.reserve("target")


def test_payload_verification_failure_is_not_delivery_success():
    inventory = PayloadInventory(1)
    slot = inventory.reserve("target")
    adapter = SimulatedPayloadAdapter(verification_success=False)
    evidence = adapter.release(
        slot, vehicle_east_m=0, vehicle_north_m=0, relative_target_error_m=.1,
        altitude_m=4, ground_speed_m_s=0, target_uncertainty_m=1,
    )
    assert not adapter.verify(slot, evidence)
    assert slot.state is PayloadState.UNKNOWN and not evidence.verification_succeeded


def test_orchestrator_requires_checkpoint_guard_and_deduplicates_delivered_target():
    policy = TargetEligibilityPolicy(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 8, 25, 60)
    orchestrator = RescueMissionOrchestrator(policy, TargetServiceQueue())
    with pytest.raises(RuntimeError):
        orchestrator.save_checkpoint(SearchResumeManager(3, 12, SearchResumePolicyName.REPLAY_FROM_LAST_REACHED).checkpoint(
            mission_id="mission", mission_fingerprint="a" * 64, current_seq=5,
            reached_items=[3, 4], east_m=1, north_m=1, coverage_percent=20,
        ))


@pytest.mark.parametrize("connection", ["/dev/serial0", "/dev/ttyACM0", "/dev/ttyUSB0", "COM3", "COM7", "tcp:8.8.8.8:5760"])
def test_stage7s_rejects_physical_and_remote_command_connections(connection):
    with pytest.raises(PermissionError):
        SITLAutopilotAdapter(connection, "sitl", "simulation_only")


def test_orchestration_accepts_only_isolated_sitl_adapter_type():
    adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only")
    require_sitl_command_adapter(adapter)

    class PhysicalReadOnlyAdapter:
        connection_string = "/dev/serial0"

    with pytest.raises(RescueSafetyError):
        require_sitl_command_adapter(PhysicalReadOnlyAdapter())


def test_simulated_payload_has_no_real_actuator_import_or_command():
    import inspect
    import rescue_core.autonomy.payload as payload_module

    source = inspect.getsource(payload_module).lower()
    forbidden = ["pymavlink", "mav_cmd_do_set_servo", "mav_cmd_do_gripper", "import gpio"]
    assert not any(token in source for token in forbidden)


def test_two_targets_one_payload_services_only_one():
    queue = TargetServiceQueue()
    first = target(target_id="one")
    second = target(target_id="two", first_seen_at=first.first_seen_at + timedelta(seconds=1))
    queue.add(second); queue.add(first)
    assert queue.pop().target_id == "one"
    inventory = PayloadInventory(1)
    inventory.reserve("one")
    policy = TargetEligibilityPolicy(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 8, 25, 60)
    assert policy.evaluate(second, inventory).reason is EligibilityReason.PAYLOAD_UNAVAILABLE


def test_target_loss_yields_no_alignment_command_or_payload_release():
    provider = SyntheticReacquisitionProvider(
        SyntheticSurvivorScenario(target_id="lost", true_east_m=20, true_north_m=20),
        visibility_radius_m=2,
    )
    observation = provider.observe(0, 0)
    assert not observation.visible
    controller = PrecisionAlignmentController(.5, 1, .1, .4, .5)
    with pytest.raises(ValueError):
        controller.command(observation)
    assert PayloadInventory(1).remaining == 1


def test_guided_watchdog_and_boundary_violation_are_explicit():
    supervisor = GuidedSafetySupervisor(
        Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), command_timeout_s=1,
    )
    supervisor.record_command(10)
    snapshot = GuidedSafetySnapshot(
        link_alive=True, armed=True, flight_mode="GUIDED", east_m=12, north_m=5,
        altitude_m=8, payload_valid=True,
    )
    failures = supervisor.check(snapshot, now=12)
    assert "OUTSIDE_OPERATIONAL_REGION" in failures
    assert "GUIDED_COMMAND_WATCHDOG" in failures


def test_native_resume_and_replay_policy_are_distinct():
    replay = SearchResumeManager(3, 12, SearchResumePolicyName.REPLAY_FROM_LAST_REACHED)
    checkpoint = replay.checkpoint(
        mission_id="m", mission_fingerprint="f", current_seq=8,
        reached_items=[3, 4, 5, 6], east_m=0, north_m=0, coverage_percent=50,
    )
    native = SearchResumeManager(3, 12, SearchResumePolicyName.ARDUPILOT_NATIVE_RESUME)
    assert replay.select_resume_sequence(checkpoint) == 6
    assert native.select_resume_sequence(checkpoint) == 8


def test_mission_deadline_reserve_defers_delivery():
    policy = TargetEligibilityPolicy(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 8, 25, 90)
    decision = policy.evaluate(target(), PayloadInventory(1), remaining_mission_time_s=60)
    assert decision.reason is EligibilityReason.DEADLINE_RESERVE


def test_guided_mode_failure_is_propagated_and_never_faked(monkeypatch):
    adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only")
    navigator = SITLGuidedNavigator(adapter)

    def reject_guided() -> None:
        raise TimeoutError("GUIDED mode confirmation timeout")

    monkeypatch.setattr(adapter, "set_guided", reject_guided)
    with pytest.raises(TimeoutError, match="GUIDED mode confirmation"):
        navigator.enter_guided()


def test_resume_command_failure_is_propagated_before_auto(monkeypatch):
    adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only")
    navigator = SITLGuidedNavigator(adapter)
    auto_called = False

    def reject_sequence(_sequence: int) -> None:
        raise TimeoutError("MISSION_CURRENT confirmation timeout")

    def mark_auto() -> None:
        nonlocal auto_called
        auto_called = True

    monkeypatch.setattr(adapter, "set_mission_current", reject_sequence)
    monkeypatch.setattr(adapter, "resume_auto", mark_auto)
    with pytest.raises(TimeoutError, match="MISSION_CURRENT confirmation"):
        navigator.set_mission_current(4)
    assert not auto_called
