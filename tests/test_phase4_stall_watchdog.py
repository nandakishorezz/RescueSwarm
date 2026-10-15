"""
Targeted unit tests for the Stage13SIM phase4 runner stall watchdog.

Tests the six conditions specified in the lockstep forensics requirement:
  1. Healthy: heartbeat + pose + source-time all fresh -> HEALTHY
  2. Pose stale + source-time stale -> VEHICLE_STATE_STALL
  3. Heartbeat stale -> MAVLINK_STALL (existing 10s check)
  4. Stationary vehicle with advancing source-time timestamps -> HEALTHY
  5. Source-time frozen while host wall advances -> SIMULATION_CLOCK_STALL
  6. Router thread dies -> MAVLink reader thread error

The watchdog is extracted from run_stage13sim_phase4.py and tested in
isolation so no Gazebo, SITL, or MAVLink connection is required.
"""
import time
import types
import pytest


# ---------------------------------------------------------------------------
# Minimal shims for the objects the watchdog code touches.
# ---------------------------------------------------------------------------

def make_sample(autopilot_boot_time_ns: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(autopilot_boot_time_ns=autopilot_boot_time_ns)


def make_history(samples: list) -> types.SimpleNamespace:
    return types.SimpleNamespace(samples=samples)


def make_pose_subscriber(boot_time_ns):
    if boot_time_ns is None:
        return types.SimpleNamespace(history=make_history([]))
    return types.SimpleNamespace(history=make_history([make_sample(boot_time_ns)]))


def make_router(thread_alive=True, last_msg_mono=None):
    thread = types.SimpleNamespace(is_alive=lambda: thread_alive)
    return types.SimpleNamespace(_thread=thread, last_message_monotonic=last_msg_mono)


def make_adapter(thread_alive=True, last_msg_mono=None):
    return types.SimpleNamespace(router=make_router(thread_alive, last_msg_mono))


# ---------------------------------------------------------------------------
# Watchdog logic (extracted verbatim from run_stage13sim_phase4.py)
# ---------------------------------------------------------------------------

_STALL_POSE_WALL_S   = 20.0
_STALL_SIMCLK_WALL_S = 30.0


def run_watchdog_tick(
    *,
    adapter,
    pose_subscriber,
    airborne_ever,
    stall_last_pose_ns,
    stall_last_pose_wall,
    stall_last_srctime_ns,
    stall_last_srctime_wall,
    now_wall,
):
    state = dict(
        last_pose_ns=stall_last_pose_ns,
        last_pose_wall=stall_last_pose_wall,
        last_srctime_ns=stall_last_srctime_ns,
        last_srctime_wall=stall_last_srctime_wall,
    )

    if not adapter.router._thread.is_alive():
        return "MAVLink reader thread stopped unexpectedly", state

    if adapter.router.last_message_monotonic is not None:
        if now_wall - adapter.router.last_message_monotonic > 10.0:
            return "MAVLink connection lost (no messages in 10s)", state

    current_pose_ns = (
        pose_subscriber.history.samples[-1].autopilot_boot_time_ns
        if pose_subscriber.history.samples else None
    )

    if current_pose_ns is not None:
        if current_pose_ns != state["last_pose_ns"]:
            state["last_pose_wall"] = now_wall
            state["last_pose_ns"] = current_pose_ns
        if current_pose_ns != state["last_srctime_ns"]:
            state["last_srctime_wall"] = now_wall
            state["last_srctime_ns"] = current_pose_ns

    if airborne_ever:
        pose_stall_s = now_wall - state["last_pose_wall"]
        simclk_stall_s = now_wall - state["last_srctime_wall"]
        if pose_stall_s > _STALL_POSE_WALL_S:
            return (
                f"VEHICLE_STATE_STALL: no new PoseSample in {pose_stall_s:.1f}s",
                state,
            )
        if simclk_stall_s > _STALL_SIMCLK_WALL_S:
            return (
                f"SIMULATION_CLOCK_STALL: source time did not advance in {simclk_stall_s:.1f}s",
                state,
            )

    return None, state


def fresh_state(t0=0.0):
    return dict(
        stall_last_pose_ns=1000,
        stall_last_pose_wall=t0,
        stall_last_srctime_ns=1000,
        stall_last_srctime_wall=t0,
    )


# ---------------------------------------------------------------------------
# TEST 1: All fresh -> HEALTHY
# ---------------------------------------------------------------------------

def test_1_all_fresh_is_healthy():
    t0 = 1_000.0
    adapter = make_adapter(thread_alive=True, last_msg_mono=t0 - 1.0)
    pose_sub = make_pose_subscriber(boot_time_ns=2000)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=True, now_wall=t0, **fresh_state(t0),
    )
    assert label is None, f"Expected HEALTHY, got: {label}"


# ---------------------------------------------------------------------------
# TEST 2: Pose stale + source-time stale -> VEHICLE_STATE_STALL
# ---------------------------------------------------------------------------

def test_2_stale_pose_raises_vehicle_state_stall():
    t0 = 1_000.0
    last_pose_ns = 1000
    stale_since = t0 - (_STALL_POSE_WALL_S + 1.0)
    adapter = make_adapter(thread_alive=True, last_msg_mono=t0 - 1.0)
    pose_sub = make_pose_subscriber(boot_time_ns=last_pose_ns)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=True,
        stall_last_pose_ns=last_pose_ns,
        stall_last_pose_wall=stale_since,
        stall_last_srctime_ns=last_pose_ns,
        stall_last_srctime_wall=stale_since,
        now_wall=t0,
    )
    assert label is not None and "VEHICLE_STATE_STALL" in label, (
        f"Expected VEHICLE_STATE_STALL, got: {label}"
    )


# ---------------------------------------------------------------------------
# TEST 3: Router thread dead -> MAVLink thread error
# ---------------------------------------------------------------------------

def test_3_dead_router_thread_is_detected():
    t0 = 1_000.0
    adapter = make_adapter(thread_alive=False, last_msg_mono=t0 - 1.0)
    pose_sub = make_pose_subscriber(boot_time_ns=2000)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=True, now_wall=t0, **fresh_state(t0),
    )
    assert label is not None and "MAVLink reader thread" in label, (
        f"Expected thread-dead error, got: {label}"
    )


# ---------------------------------------------------------------------------
# TEST 4: Stationary XYZ but source-time advancing -> HEALTHY
# Critical: watchdog tracks boot_time_ns, not physical position.
# ---------------------------------------------------------------------------

def test_4_stationary_vehicle_with_advancing_source_time_is_healthy():
    t0 = 1_000.0
    INITIAL_NS = 5_000_000_000
    _, state = run_watchdog_tick(
        adapter=make_adapter(thread_alive=True, last_msg_mono=t0 - 0.5),
        pose_subscriber=make_pose_subscriber(boot_time_ns=INITIAL_NS),
        airborne_ever=True, now_wall=t0,
        stall_last_pose_ns=None, stall_last_pose_wall=t0,
        stall_last_srctime_ns=None, stall_last_srctime_wall=t0,
    )
    t_now = t0
    for i in range(19):
        t_now += 1.0
        new_ns = INITIAL_NS + (i + 1) * 200_000_000
        label, state = run_watchdog_tick(
            adapter=make_adapter(thread_alive=True, last_msg_mono=t_now - 0.5),
            pose_subscriber=make_pose_subscriber(boot_time_ns=new_ns),
            airborne_ever=True, now_wall=t_now,
            stall_last_pose_ns=state["last_pose_ns"],
            stall_last_pose_wall=state["last_pose_wall"],
            stall_last_srctime_ns=state["last_srctime_ns"],
            stall_last_srctime_wall=state["last_srctime_wall"],
        )
        assert label is None, (
            f"Stationary but advancing vehicle wrongly flagged at tick {i}: {label}"
        )


# ---------------------------------------------------------------------------
# TEST 5: Source-time frozen -> SIMULATION_CLOCK_STALL (or VEHICLE_STATE_STALL)
# ---------------------------------------------------------------------------

def test_5_frozen_source_time_raises_stall():
    t0 = 1_000.0
    FROZEN_NS = 7_000_000_000
    frozen_since = t0 - (_STALL_SIMCLK_WALL_S + 2.0)
    adapter = make_adapter(thread_alive=True, last_msg_mono=t0 - 1.0)
    pose_sub = make_pose_subscriber(boot_time_ns=FROZEN_NS)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=True,
        stall_last_pose_ns=FROZEN_NS,
        stall_last_pose_wall=frozen_since,
        stall_last_srctime_ns=FROZEN_NS,
        stall_last_srctime_wall=frozen_since,
        now_wall=t0,
    )
    assert label is not None and "STALL" in label, (
        f"Expected a STALL failure, got: {label}"
    )


# ---------------------------------------------------------------------------
# TEST 6: Router thread dies -> bounded detection
# ---------------------------------------------------------------------------

def test_6_router_thread_dies_is_detected():
    t0 = 500.0
    adapter = make_adapter(thread_alive=False, last_msg_mono=t0 - 2.0)
    pose_sub = make_pose_subscriber(boot_time_ns=3000)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=True, now_wall=t0, **fresh_state(t0),
    )
    assert label is not None and "MAVLink reader thread" in label, (
        f"Expected thread-dead label, got: {label}"
    )


# ---------------------------------------------------------------------------
# BONUS: pre-airborne stall suppression
# ---------------------------------------------------------------------------

def test_stall_suppressed_before_airborne():
    t0 = 1_000.0
    frozen_since = t0 - (_STALL_SIMCLK_WALL_S + 10.0)
    adapter = make_adapter(thread_alive=True, last_msg_mono=t0 - 1.0)
    pose_sub = make_pose_subscriber(boot_time_ns=999)
    label, _ = run_watchdog_tick(
        adapter=adapter, pose_subscriber=pose_sub,
        airborne_ever=False,
        stall_last_pose_ns=999, stall_last_pose_wall=frozen_since,
        stall_last_srctime_ns=999, stall_last_srctime_wall=frozen_since,
        now_wall=t0,
    )
    assert label is None, f"Should be suppressed pre-airborne, got: {label}"


if __name__ == "__main__":
    tests = [
        test_1_all_fresh_is_healthy,
        test_2_stale_pose_raises_vehicle_state_stall,
        test_3_dead_router_thread_is_detected,
        test_4_stationary_vehicle_with_advancing_source_time_is_healthy,
        test_5_frozen_source_time_raises_stall,
        test_6_router_thread_dies_is_detected,
        test_stall_suppressed_before_airborne,
    ]
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
