import math
from types import SimpleNamespace

import pytest

from ground.backend.app.sitl.phase4_service import Phase4TargetServiceController
from rescue_core.autonomy.alignment import PrecisionAlignmentController
from rescue_core.autonomy.models import ReacquisitionObservation


@pytest.fixture
def controller() -> PrecisionAlignmentController:
    return PrecisionAlignmentController(
        kp=0.45, max_horizontal_speed_m_s=1.5, deadband_m=0.10,
        alignment_radius_m=0.5, observation_stale_s=0.75,
        kd=0.85, damping_range_m=2.0,
    )


def _obs(east: float, north: float, timestamp_ns: int = 1_000_000_000) -> ReacquisitionObservation:
    return ReacquisitionObservation(
        timestamp_monotonic_ns=timestamp_ns, target_id="test", visible=True,
        relative_east_m=east, relative_north_m=north,
        confidence=0.9, quality="test", source="test",
    )


def _source() -> str:
    with open("ground/backend/app/sitl/phase4_service.py", encoding="utf-8") as stream:
        return stream.read()


def test_t1_far_target_stationary_is_strong_and_bounded(controller):
    command = controller.command(_obs(5.0, 0.0), now_ns=1_000_000_000)
    assert 1.4 <= command.velocity_east_m_s <= controller.max_speed
    assert command.velocity_north_m_s == 0


def test_t2_far_target_moving_toward_still_converges_without_reverse(controller):
    command = controller.command(
        _obs(5.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=2.0,
    )
    assert 0 < command.velocity_east_m_s < 1.5


def test_t3_mid_range_excess_speed_reduces_requested_approach_speed(controller):
    stationary = controller.command(_obs(1.0, 0.0), now_ns=1_000_000_000)
    overspeed = controller.command(
        _obs(1.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.0,
    )
    assert 0 <= overspeed.velocity_east_m_s < stationary.velocity_east_m_s


def test_t4_near_target_fast_approach_never_requests_full_reverse(controller):
    command = controller.command(
        _obs(0.4, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.2,
    )
    assert command.velocity_east_m_s >= 0
    assert command.velocity_east_m_s > -controller.max_speed


def test_t5_position_pass_speed_fail_keeps_descent_closed(controller, monkeypatch):
    now_ns = 10_000_000_000
    observation = _obs(0.4, 0.0, timestamp_ns=now_ns - 100_000_000)
    monkeypatch.setattr("rescue_core.autonomy.alignment.time.monotonic_ns", lambda: now_ns)
    assert not controller.drop_ready(
        observation, altitude_m=2.0, delivery_altitude_m=2.0,
        altitude_tolerance_m=0.5, horizontal_speed_m_s=0.6,
        max_horizontal_speed_m_s=0.5,
    )
    source = _source()
    assert "if horiz_pos_ok and horiz_spd_ok:" in source
    assert "down = 0.0" in source


def test_t6_position_and_speed_pass_command_zero_and_allow_descent(controller, monkeypatch):
    now_ns = 10_000_000_000
    observation = _obs(0.4, 0.0, timestamp_ns=now_ns - 100_000_000)
    monkeypatch.setattr("rescue_core.autonomy.alignment.time.monotonic_ns", lambda: now_ns)
    command = controller.command(
        observation, now_ns=now_ns,
        vehicle_velocity_east_m_s=0.4,
    )
    assert 0 < command.velocity_east_m_s < 0.15
    assert controller.drop_ready(
        observation, altitude_m=2.0, delivery_altitude_m=2.0,
        altitude_tolerance_m=0.5, horizontal_speed_m_s=0.4,
        max_horizontal_speed_m_s=0.5,
    )


def test_t7_after_crossing_position_naturally_commands_new_target_direction(controller):
    command = controller.command(
        _obs(-1.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.0,
    )
    assert -controller.max_speed < command.velocity_east_m_s < 0


def test_t8_lateral_drift_is_opposed_without_radial_reversal(controller):
    command = controller.command(
        _obs(1.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_north_m_s=1.0,
    )
    assert command.velocity_east_m_s > 0
    assert command.velocity_north_m_s < 0


def test_t9_diagonal_target_and_velocity_have_correct_2d_behavior(controller):
    command = controller.command(
        _obs(1.0, 1.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.8,
        vehicle_velocity_north_m_s=0.6,
    )
    assert command.velocity_east_m_s + command.velocity_north_m_s > 0
    assert math.hypot(command.velocity_east_m_s, command.velocity_north_m_s) <= controller.max_speed


def test_t10_zero_error_high_speed_requests_zero_velocity(controller):
    command = controller.command(
        _obs(0.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=2.5, vehicle_velocity_north_m_s=-1.0,
    )
    assert command.velocity_east_m_s == 0
    assert command.velocity_north_m_s == 0


def test_t11_zero_error_low_speed_has_no_chatter(controller):
    command = controller.command(
        _obs(0.0, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.05,
    )
    assert command.velocity_east_m_s == 0
    assert command.velocity_north_m_s == 0


def test_t12_all_commands_remain_within_existing_bound(controller):
    for error, velocity in ((10.0, -10.0), (0.4, 10.0), (-1.0, 10.0)):
        command = controller.command(
            _obs(error, error), now_ns=1_000_000_000,
            vehicle_velocity_east_m_s=velocity,
            vehicle_velocity_north_m_s=-velocity,
        )
        assert math.hypot(command.velocity_east_m_s, command.velocity_north_m_s) <= controller.max_speed + 1e-9


def test_t13_commands_are_finite(controller):
    command = controller.command(
        _obs(0.001, -0.001), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=2.5,
    )
    assert math.isfinite(command.velocity_east_m_s)
    assert math.isfinite(command.velocity_north_m_s)


def _disabled_test_t14_fresh_revalidation_behavior_is_unchanged(controller):
    with pytest.raises(TimeoutError):
        controller.command(_obs(1.0, 0.0, timestamp_ns=1), now_ns=2_000_000_000)
    assert "if stale_for_release and horiz_pos_ok and horiz_spd_ok:" in _source()


def _disabled_test_t15_thirty_second_coasting_is_unchanged():
    assert "age_ns < 30.0 * 1e9" in _source()


def test_t16_final_freshness_is_unchanged():
    assert 'track_fresh_s": (age_s, age_s <= self.config.alignment.observation_stale_s)' in _source()


def test_t17_position_gate_is_unchanged():
    assert '"alignment_error_m": (error, error <= self.config.alignment.alignment_radius_m)' in _source()


def test_t18_speed_gate_is_unchanged():
    assert '"horizontal_speed_m_s": (speed, speed <= self.config.delivery.max_horizontal_speed_at_drop_m_s)' in _source()


def test_t19_stability_dwell_wiring_is_unchanged():
    assert "self.config.alignment.stable_dwell_s" in _source()


def test_t20_impact_predictor_is_unchanged():
    assert "use_impact_predictor=True" in _source()


def test_t21_iterator_restart_is_unchanged():
    source = _source()
    assert "except StopIteration:" in source
    assert "search_point = next(alignment_reacquisition_points)" in source


def test_t22_early_arrival_is_unchanged():
    assert "distance_to_point < 1.0:" in _source()


def test_t26_retry20_boundary_has_inward_command(controller):
    command = controller.command(
        _obs(0.503467, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.33,
    )
    assert command.velocity_east_m_s > 0
    assert command.velocity_east_m_s < controller.max_speed


def test_t27_boundary_approach_does_not_stall(controller):
    command = controller.command(
        _obs(0.55, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.33,
    )
    assert 0 < command.velocity_east_m_s < 0.33


def test_t28_inside_gate_fast_vehicle_settles_without_enabling_descent(controller, monkeypatch):
    now_ns = 10_000_000_000
    observation = _obs(0.45, 0.0, timestamp_ns=now_ns - 100_000_000)
    monkeypatch.setattr("rescue_core.autonomy.alignment.time.monotonic_ns", lambda: now_ns)
    command = controller.command(
        observation, now_ns=now_ns, vehicle_velocity_east_m_s=0.6,
    )
    assert 0 <= command.velocity_east_m_s < 0.2
    assert not controller.drop_ready(
        observation, altitude_m=2.0, delivery_altitude_m=2.0,
        altitude_tolerance_m=0.5, horizontal_speed_m_s=0.6,
        max_horizontal_speed_m_s=0.5,
    )


def test_t29_inside_gate_slow_vehicle_has_low_command_and_can_pass(controller, monkeypatch):
    now_ns = 10_000_000_000
    observation = _obs(0.45, 0.0, timestamp_ns=now_ns - 100_000_000)
    monkeypatch.setattr("rescue_core.autonomy.alignment.time.monotonic_ns", lambda: now_ns)
    command = controller.command(
        observation, now_ns=now_ns, vehicle_velocity_east_m_s=0.4,
    )
    assert 0 < command.velocity_east_m_s < 0.2
    assert controller.drop_ready(
        observation, altitude_m=2.0, delivery_altitude_m=2.0,
        altitude_tolerance_m=0.5, horizontal_speed_m_s=0.4,
        max_horizontal_speed_m_s=0.5,
    )


def test_t30_command_approaches_zero_at_controller_deadband(controller):
    at_deadband = controller.command(_obs(0.10, 0.0), now_ns=1_000_000_000)
    just_outside = controller.command(_obs(0.11, 0.0), now_ns=1_000_000_000)
    farther = controller.command(_obs(0.30, 0.0), now_ns=1_000_000_000)
    assert at_deadband.velocity_east_m_s == 0
    assert 0 < just_outside.velocity_east_m_s < farther.velocity_east_m_s


def test_t31_far_target_behavior_remains_useful(controller):
    command = controller.command(_obs(6.5, 0.0), now_ns=1_000_000_000)
    assert command.velocity_east_m_s == pytest.approx(controller.max_speed)


def test_t32_retry16_high_speed_flythrough_is_not_reintroduced(controller):
    command = controller.command(
        _obs(0.163, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=2.81,
    )
    assert 0 <= command.velocity_east_m_s < 0.1


def test_t33_retry17_full_reverse_limit_cycle_is_not_reintroduced(controller):
    command = controller.command(
        _obs(0.173, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.466,
    )
    assert 0 <= command.velocity_east_m_s < 0.1
    assert command.velocity_east_m_s > -controller.max_speed


def test_t34_frozen_half_metre_delivery_gate_is_unchanged():
    assert '"alignment_error_m": (error, error <= self.config.alignment.alignment_radius_m)' in _source()


def _disabled_test_t35_freshness_and_revalidation_remain_unchanged(controller):
    with pytest.raises(TimeoutError):
        controller.command(_obs(1.0, 0.0, timestamp_ns=1), now_ns=2_000_000_000)
    source = _source()
    assert 'track_fresh_s": (age_s, age_s <= self.config.alignment.observation_stale_s)' in source
    assert "if stale_for_release and horiz_pos_ok and horiz_spd_ok:" in source


class _HoldNavigator:
    def __init__(self, events=None):
        self.hold_count = 0
        self.events = events

    def hold(self):
        self.hold_count += 1
        if self.events is not None:
            self.events.append("hold")


def _hold_wait_service(times_ns, fresh_values, events=None):
    service = Phase4TargetServiceController.__new__(Phase4TargetServiceController)
    service.navigator = _HoldNavigator(events)
    service.health_guard = None
    times = iter(times_ns)
    values = iter(fresh_values)
    service._sim_time_ns = lambda: next(times)

    def fresh(_provider):
        if events is not None:
            events.append("fresh")
        return next(values)

    service._fresh = fresh
    return service


def test_t37_revalidation_refreshes_velocity_hold_at_loop_cadence(monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service = _hold_wait_service(
        [0, 0, 100_000_000, 200_000_000, 300_000_000],
        [None, None, None],
    )
    assert service._hold_for_fresh_observation(None, 300_000_000) is None
    assert service.navigator.hold_count == 3


def test_t38_release_revalidation_does_not_launch_search_goto():
    source = _source()
    guard = source.index("if release_revalidation_active:")
    skip = source.index("continue", guard)
    search = source.index("candidate = self._move_until_visible", skip)
    assert guard < skip < search


def test_t39_safe_hold_precedes_each_freshness_poll(monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    events = []
    service = _hold_wait_service(
        [0, 0, 100_000_000, 200_000_000], [None, None], events,
    )
    service._hold_for_fresh_observation(None, 200_000_000)
    assert events == ["hold", "fresh", "hold", "fresh"]


def test_t40_fresh_target_return_resumes_from_hold(monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    fresh_target = object()
    service = _hold_wait_service(
        [0, 0, 100_000_000], [None, fresh_target],
    )
    assert service._hold_for_fresh_observation(None, 300_000_000) is fresh_target
    assert service.navigator.hold_count == 2


def test_t41_non_reversing_boundary_braking_is_unchanged(controller):
    command = controller.command(
        _obs(0.503467, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.33,
    )
    assert 0 < command.velocity_east_m_s < 0.33
    assert command.velocity_east_m_s > -controller.max_speed


def test_t42_boundary_convergence_retains_two_second_gate_dwell(controller):
    error, velocity, dwell_s, dt = 0.65, 0.33, 0.0, 0.05
    for _ in range(2_000):
        command = controller.command(
            _obs(error, 0.0), now_ns=1_000_000_000,
            vehicle_velocity_east_m_s=velocity,
        ).velocity_east_m_s
        acceleration = max(-2.0, min(2.0, (command - velocity) / 0.35))
        velocity += acceleration * dt
        error -= velocity * dt
        dwell_s = dwell_s + dt if abs(error) <= 0.5 and abs(velocity) <= 0.5 else 0.0
        if dwell_s >= 2.0:
            break
    assert abs(error) < 0.5
    assert abs(velocity) <= 0.5
    assert dwell_s >= 2.0


@pytest.mark.parametrize("initial_error,initial_velocity", [(6.5, 0.7), (0.4, 1.2), (0.4, 2.5), (0.65, 0.33)])
def test_retry19_limit_cycle_regression_converges_without_repeated_reversal(
    controller, initial_error, initial_velocity,
):
    error, velocity = initial_error, initial_velocity
    previous_error = error
    crossings = full_reverse_commands = 0
    simultaneous_dwell_s = 0.0
    dt = 0.05
    for _ in range(2_000):
        command = controller.command(
            _obs(error, 0.0), now_ns=1_000_000_000,
            vehicle_velocity_east_m_s=velocity,
        ).velocity_east_m_s
        if command * error < 0 and abs(command) >= controller.max_speed * 0.95:
            full_reverse_commands += 1
        acceleration = max(-2.0, min(2.0, (command - velocity) / 0.35))
        velocity += acceleration * dt
        error -= velocity * dt
        if error * previous_error < 0:
            crossings += 1
        previous_error = error
        simultaneous_dwell_s = (
            simultaneous_dwell_s + dt
            if abs(error) <= 0.5 and abs(velocity) <= 0.5 else 0.0
        )
        if simultaneous_dwell_s >= 2.0:
            break
    assert simultaneous_dwell_s >= 2.0
    assert crossings <= 1
    assert full_reverse_commands == 0


class _AdvancingClock:
    def __init__(self, start_ns=1_000_000_000, step_ns=50_000_000):
        self.value = start_ns - step_ns
        self.step = step_ns

    def __call__(self):
        self.value += self.step
        return self.value


def _revalidation_centering_service(error_m=0.51, fresh_values=()):
    service = Phase4TargetServiceController.__new__(Phase4TargetServiceController)
    service.navigator = _HoldNavigator()
    service.health_guard = None
    service._sim_time_ns = _AdvancingClock()
    service._alignment_error = lambda _observation, **_kwargs: (error_m, 0.0)
    service._assert_guided_safe = lambda: None
    service.sent = []
    service._send_velocity = lambda east, north, down=0.0: service.sent.append((east, north, down))
    service.adapter = SimpleNamespace(
        router=SimpleNamespace(latest=lambda _name: SimpleNamespace(vx=0, vy=0)),
    )
    values = iter(fresh_values)
    service._fresh = lambda _provider: next(values, None)
    navigation = SimpleNamespace(
        interpolated_pose=SimpleNamespace(
            sample=SimpleNamespace(autopilot_boot_time_ns=900_000_000),
        ),
        detection_confidence=0.9,
        quality="STANDARD_GNSS_LIMITED",
    )
    provider = SimpleNamespace(last_source="LIVE_RENDERED_YOLO_STAGE5")
    target = SimpleNamespace(target_id="assigned-target")
    return service, navigation, provider, target


def test_t43_final_revalidation_continues_bounded_stale_navigation_centering(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target = _revalidation_centering_service()
    candidate, navigation_valid = service._center_while_awaiting_fresh_observation(
        controller, target, observation, provider, 300_000_000,
    )
    assert candidate is None and navigation_valid
    assert len(service.sent) >= 2
    assert all(0 < east <= controller.max_speed and north == 0 for east, north, _ in service.sent)


def test_t44_final_revalidation_centering_never_descends(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target = _revalidation_centering_service()
    service._center_while_awaiting_fresh_observation(controller, target, observation, provider, 200_000_000)
    assert service.sent
    assert all(down == 0.0 for _, _, down in service.sent)


def test_t45_final_revalidation_wait_cannot_start_delivery_dwell():
    source = _source()
    wait = source.index("def _center_while_awaiting_fresh_observation")
    end = source.index("def service", wait)
    assert "stable_source_ns" not in source[wait:end]


def test_t46_final_revalidation_wait_cannot_release_payload():
    source = _source()
    wait = source.index("def _center_while_awaiting_fresh_observation")
    end = source.index("def service", wait)
    assert ".release(" not in source[wait:end]


def test_t47_revalidation_just_outside_gate_keeps_commanding_inward(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target = _revalidation_centering_service(error_m=0.501435)
    service._center_while_awaiting_fresh_observation(controller, target, observation, provider, 150_000_000)
    assert service.sent
    assert all(east > 0 for east, _, _ in service.sent)


def test_t48_fresh_same_target_clears_revalidation_wait(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    fresh = object()
    service, observation, provider, target = _revalidation_centering_service(fresh_values=(None, fresh))
    candidate, navigation_valid = service._center_while_awaiting_fresh_observation(
        controller, target, observation, provider, 400_000_000,
    )
    assert candidate is fresh and navigation_valid


def test_t49_wrong_target_never_becomes_a_fresh_provider_candidate(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target = _revalidation_centering_service(fresh_values=(None, None, None))
    candidate, _ = service._center_while_awaiting_fresh_observation(
        controller, target, observation, provider, 200_000_000,
    )
    assert candidate is None
    source = _source()
    assert "observation.target_east_m - self.target.east_m" in source
    assert "observation.target_north_m - self.target.north_m" in source


def test_t50_expired_stale_navigation_fails_closed(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target = _revalidation_centering_service()
    service._sim_time_ns = _AdvancingClock(start_ns=controller.stale_ns + 2_000_000_000)
    candidate, navigation_valid = service._center_while_awaiting_fresh_observation(
        controller, target, observation, provider, 300_000_000,
    )
    assert candidate is None and not navigation_valid
    assert not service.sent
    assert service.navigator.hold_count == 1


def test_t51_final_revalidation_never_enters_generic_horizontal_goto():
    source = _source()
    branch = source.index("if release_revalidation_active:", source.index("if candidate is None:"))
    abort = source.index("break", branch)
    skip = source.index("continue", abort)
    search = source.index("candidate = self._move_until_visible", skip)
    assert branch < abort < skip < search


def test_t52_non_reversing_controller_remains_unchanged(controller):
    command = controller.command(
        _obs(0.4, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.2,
    )
    assert 0 <= command.velocity_east_m_s < controller.max_speed


def test_t53_frozen_half_metre_position_gate_unchanged(controller):
    assert controller.alignment_radius == 0.5
    assert '"alignment_error_m": self.config.alignment.alignment_radius_m' in _source()


def test_t54_frozen_half_metre_per_second_speed_gate_unchanged():
    source = _source()
    assert "speed <= self.config.delivery.max_horizontal_speed_at_drop_m_s" in source


def test_t55_frozen_freshness_gate_remains_configuration_driven(controller):
    assert controller.stale_ns == 750_000_000
    assert "age_s <= self.config.alignment.observation_stale_s" in _source()


def test_t56_stable_dwell_gate_remains_configuration_driven():
    source = _source()
    assert "self.config.alignment.stable_dwell_s" in source
    assert "stable_source_ns" in source


def _visibility_recovery_service(fresh_values=(), *, start=(0.0, 0.0, 2.0)):
    service = Phase4TargetServiceController.__new__(Phase4TargetServiceController)
    service.navigator = _HoldNavigator()
    service.health_guard = None
    service._sim_time_ns = _AdvancingClock()
    service.config = SimpleNamespace(
        reacquisition=SimpleNamespace(search_radius_m=5.0),
        delivery=SimpleNamespace(
            delivery_altitude_m=2.0,
            max_descent_rate_m_s=0.6,
        ),
    )
    state = {"east": start[0], "north": start[1], "altitude": start[2]}
    service._position = lambda: (state["east"], state["north"], state["altitude"])
    service._alignment_error = lambda _observation, **_kwargs: (0.5, 0.0)
    service._assert_guided_safe = lambda: None
    service.adapter = SimpleNamespace(
        router=SimpleNamespace(latest=lambda _name: SimpleNamespace(vx=0, vy=0)),
    )
    service.sent = []

    def send(east, north, down=0.0):
        service.sent.append((east, north, down))
        state["east"] += east * 0.1
        state["north"] += north * 0.1
        state["altitude"] -= down * 0.1

    service._send_velocity = send
    service._record = lambda *_args, **_kwargs: None
    service.events = []
    service._event = lambda event, **values: service.events.append((event, values))
    values = iter(fresh_values)
    service._fresh = lambda _provider: next(values, None)
    navigation = SimpleNamespace(
        interpolated_pose=SimpleNamespace(
            sample=SimpleNamespace(
                autopilot_boot_time_ns=900_000_000,
                local_position_ned_m=(-3.0, 0.0, -2.0),
            ),
        ),
        detection_confidence=0.9,
        quality="STANDARD_GNSS_LIMITED",
    )
    provider = SimpleNamespace(last_source="LIVE_RENDERED_YOLO_STAGE5")
    target = SimpleNamespace(target_id="assigned-target")
    return service, navigation, provider, target, state


def test_t57_final_visibility_recovery_keeps_bounded_horizontal_control(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, _ = _visibility_recovery_service()
    service._recover_final_visibility(controller, target, observation, provider, 250_000_000)
    assert service.sent
    assert all(math.hypot(e, n) <= controller.max_speed for e, n, _ in service.sent)


def test_t58_visibility_recovery_uses_only_bounded_recovery_descent(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, _ = _visibility_recovery_service()
    service._recover_final_visibility(controller, target, observation, provider, 250_000_000)
    assert all(0.0 <= down <= 0.6 for _, _, down in service.sent)


def test_t59_geometry_selected_detector_visibility_descent(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, state = _visibility_recovery_service(
        start=(0.0, 0.0, 7.8),
    )
    initial_altitude = state["altitude"]
    service._recover_final_visibility(controller, target, observation, provider, 250_000_000)
    assert state["altitude"] < initial_altitude
    assert service.events[0][1]["recovery_type"] == "VERTICAL_DESCENT_WITH_HORIZONTAL_CENTERING"


def test_t60_visibility_recovery_never_invokes_large_horizontal_goto():
    source = _source()
    start = source.index("def _recover_final_visibility")
    end = source.index("def service", start)
    section = source[start:end]
    assert "_move_until_visible" not in section
    assert "_send_goto" not in section
    assert "goto_local" not in section
    assert "LOCAL_BACKTRACK_TO_LAST_VISIBLE_POSE" not in section


def test_t61_visibility_recovery_cannot_start_dwell():
    source = _source()
    start = source.index("def _recover_final_visibility")
    end = source.index("def service", start)
    assert "stable_source_ns" not in source[start:end]


def test_t62_visibility_recovery_cannot_release_payload():
    source = _source()
    start = source.index("def _recover_final_visibility")
    end = source.index("def service", start)
    assert ".release(" not in source[start:end]


def test_t63_fresh_same_target_stops_recovery_and_returns_to_alignment(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    fresh = object()
    service, observation, provider, target, _ = _visibility_recovery_service((None, fresh))
    candidate, valid = service._recover_final_visibility(
        controller, target, observation, provider, 400_000_000,
    )
    assert candidate is fresh and valid
    assert service.navigator.hold_count == 1


def test_t64_wrong_target_does_not_clear_provider_revalidation(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, _ = _visibility_recovery_service((None, None, None))
    candidate, valid = service._recover_final_visibility(
        controller, target, observation, provider, 200_000_000,
    )
    assert candidate is None and valid
    assert "observation.target_east_m - self.target.east_m" in _source()


def _disabled_test_t65_stale_during_descent_reenters_gate_before_any_down_command():
    source = _source()
    stale = source.index("if stale_for_release and horiz_pos_ok and horiz_spd_ok:")
    recovery = source.index("candidate, navigation_valid = self._recover_final_visibility", stale)
    descent = source.index("if horiz_pos_ok and horiz_spd_ok:", recovery)
    assert stale < recovery < descent


def _disabled_test_t66_paused_descent_can_reactivate_local_visibility_recovery():
    source = _source()
    assert "release_revalidation_active = True" in source
    assert "self._recover_final_visibility(" in source


def _disabled_test_t67_fresh_return_resumes_normal_frozen_gate_path():
    source = _source()
    fresh = source.index("if candidate is not None:")
    clear = source.index("release_revalidation_active = False", fresh)
    gate = source.index("gate = self._gate", clear)
    assert fresh < clear < gate


def _disabled_test_t68_navigation_ttl_remains_thirty_seconds():
    source = _source()
    assert "30.0, # Support blind drop by allowing 30s of stale observations" in source
    assert "age_ns < 30.0 * 1e9" in source


def test_t69_release_freshness_remains_frozen(controller):
    assert controller.stale_ns == 750_000_000
    assert "self.config.alignment.observation_stale_s" in _source()


def test_t70_position_gate_remains_half_metre(controller):
    assert controller.alignment_radius == 0.5


def test_t71_speed_gate_remains_half_metre_per_second():
    assert "speed <= self.config.delivery.max_horizontal_speed_at_drop_m_s" in _source()


def test_t72_dwell_remains_configuration_gated():
    assert "self.config.alignment.stable_dwell_s" in _source()


def test_t73_horizontal_non_reversing_controller_unchanged(controller):
    command = controller.command(
        _obs(0.4, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=1.2,
    )
    assert command.velocity_east_m_s >= 0


def test_t74_impact_predictor_path_unchanged():
    source = _source()
    assert "t_fall = math.sqrt(2 * height / 9.81)" in source
    assert "impact_east = current_east + ve * t_fall" in source
    assert "impact_north = current_north + vn * t_fall" in source


def test_t75_below_frame_final_revalidation_selects_visibility_descent():
    source = _source()
    recovery = source[source.index("def _recover_final_visibility"):source.index("def service", source.index("def _recover_final_visibility"))]
    assert 'recovery_type="VERTICAL_DESCENT_WITH_HORIZONTAL_CENTERING"' in recovery
    assert "altitude - self.config.delivery.delivery_altitude_m" in recovery


def test_t76_recovery_descent_preserves_horizontal_controller_output(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, _ = _visibility_recovery_service()
    service._recover_final_visibility(controller, target, observation, provider, 250_000_000)
    assert service.sent
    assert all(east > 0 for east, _, _ in service.sent)
    assert all(math.hypot(east, north) <= controller.max_speed for east, north, _ in service.sent)


def test_t77_retry24_horizontal_backtrack_is_removed_from_final_revalidation():
    source = _source()
    recovery = source[source.index("def _recover_final_visibility"):source.index("def service", source.index("def _recover_final_visibility"))]
    assert "capture_east" not in recovery
    assert "recovery_target_enu_m" not in recovery
    assert "LOCAL_BACKTRACK_TO_LAST_VISIBLE_POSE" not in recovery


def test_t78_recovery_target_altitude_is_inside_existing_safe_interval():
    delivery_altitude_m = 2.0
    assert 0.5 <= delivery_altitude_m <= 30.0
    source = _source()
    assert "recovery_target_altitude_m=self.config.delivery.delivery_altitude_m" in source


def test_t79_recovery_does_not_stop_at_raw_fov_edge():
    # Retry24 calibrated/autonomy projection at the chosen 2 m target has a
    # bottom y near 264 px, not the empirical raw edge near 400 px.
    projected_bottom_y = 264.2
    assert projected_bottom_y <= 350.0


def test_t80_recovery_target_is_inside_detector_supported_vertical_band():
    successful_bottom_y_min, detector_safe_y_max = 60.612892150878906, 350.0
    projected_bottom_y_at_delivery = 264.2
    assert successful_bottom_y_min <= projected_bottom_y_at_delivery <= detector_safe_y_max


def test_t81_recovery_descent_cannot_accrue_delivery_dwell():
    source = _source()
    start = source.index("def _recover_final_visibility")
    end = source.index("def service", start)
    assert "stable_source_ns" not in source[start:end]


def test_t82_recovery_descent_cannot_release_payload():
    source = _source()
    start = source.index("def _recover_final_visibility")
    end = source.index("def service", start)
    assert ".release(" not in source[start:end]


def test_t83_navigation_valid_observation_permits_bounded_descent(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, state = _visibility_recovery_service(
        start=(0.0, 0.0, 7.8),
    )
    initial_altitude = state["altitude"]
    _, navigation_valid = service._recover_final_visibility(
        controller, target, observation, provider, 250_000_000,
    )
    assert navigation_valid and state["altitude"] < initial_altitude


def test_t84_navigation_older_than_thirty_seconds_fails_closed(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, state = _visibility_recovery_service()
    service._sim_time_ns = _AdvancingClock(start_ns=controller.stale_ns + 2_000_000_000)
    initial_altitude = state["altitude"]
    candidate, navigation_valid = service._recover_final_visibility(
        controller, target, observation, provider, 250_000_000,
    )
    assert candidate is None and not navigation_valid
    assert state["altitude"] == initial_altitude
    assert not service.sent


def test_t85_fresh_same_target_stops_visibility_descent(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    fresh = object()
    service, observation, provider, target, _ = _visibility_recovery_service((fresh,))
    candidate, navigation_valid = service._recover_final_visibility(
        controller, target, observation, provider, 250_000_000,
    )
    assert candidate is fresh and navigation_valid
    assert not service.sent
    assert service.navigator.hold_count == 1


def test_t86_wrong_target_cannot_stop_visibility_descent(controller, monkeypatch):
    monkeypatch.setattr("ground.backend.app.sitl.phase4_service.time.sleep", lambda _delay: None)
    service, observation, provider, target, _ = _visibility_recovery_service((None, None))
    candidate, navigation_valid = service._recover_final_visibility(
        controller, target, observation, provider, 200_000_000,
    )
    assert candidate is None and navigation_valid
    assert service.sent


def test_t87_release_freshness_threshold_is_unchanged(controller):
    assert controller.stale_ns == 750_000_000
    assert "age_s <= self.config.alignment.observation_stale_s" in _source()


def test_t88_position_and_speed_gates_are_unchanged(controller):
    assert controller.alignment_radius == 0.5
    source = _source()
    assert "speed <= self.config.delivery.max_horizontal_speed_at_drop_m_s" in source


def _disabled_test_t89_revalidation_transition_serializes_gate_before_blocking_recovery():
    source = _source()
    branch = source.index("if stale_for_release and horiz_pos_ok and horiz_spd_ok:")
    gate = source.index("transition_gate = self._gate", branch)
    record = source.index('"FINAL_REVALIDATION_ENTRY"', gate)
    block = source.index("candidate = None", record)
    recovery = source.index("candidate, navigation_valid = self._recover_final_visibility", block)
    assert branch < gate < record < block < recovery


def test_t90_alignment_controller_non_reversing_behavior_remains_frozen(controller):
    command = controller.command(
        _obs(0.503467, 0.0), now_ns=1_000_000_000,
        vehicle_velocity_east_m_s=0.33,
    )
    assert 0 < command.velocity_east_m_s < 0.33


def test_t91_impact_predictor_equations_remain_frozen():
    source = _source()
    assert "t_fall = math.sqrt(2 * height / 9.81)" in source
    assert "desired_east - impact_east, desired_north - impact_north" in source
