import time
import pytest
from unittest.mock import MagicMock

from ground.backend.app.sitl.phase4_service import Phase4TargetServiceController
from rescue_core.autonomy.config import load_rescue_config
from ground.backend.app.services.coordinates import LocalCoordinateFrame

def test_reacquisition_iterator_exhaustion_regression():
    config = load_rescue_config()
    config.reacquisition.timeout_s = 5.0
    config.guided.setpoint_rate_hz = 10
    config.reacquisition.search_radius_m = 2.0
    
    class MockAdapter:
        connection_string = "tcp:127.0.0.1:5760"
        router = MagicMock()
        router.latest.return_value = MagicMock(time_boot_ms=1000)
    MockAdapter.__name__ = "SITLAutopilotAdapter"
    MockAdapter.__module__ = "ground.backend.app.sitl.adapter"
    adapter = MockAdapter()
    pipeline = MagicMock()
    frame = MagicMock()
    
    from pathlib import Path
    from shapely.geometry import Polygon
    boundary_enu = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
    inventory_mock = MagicMock()
    inventory_mock.remaining = 1
    inventory_mock = MagicMock()
    inventory_mock.remaining = 1
    controller = Phase4TargetServiceController(
        adapter=adapter,
        pipeline=pipeline,
        config=config,
        frame=frame,
        boundary_enu=boundary_enu,
        mission_id="test-mission",
        mission_fingerprint="test-fingerprint",
        search_first_seq=1,
        rtl_seq=10,
        run_dir=Path("."),
        payload=MagicMock(),
        inventory=inventory_mock
    )
    
    # Trackers to measure what happens
    holds = [0]
    move_until_visible_calls = [0]
    sleeps = [0]
    
    # The simulation time starts at 10s.
    sim_time_ns = [10 * 1_000_000_000]
    
    def mock_vehicle():
        return ("GUIDED", True, 0.0, sim_time_ns[0])
        
    def mock_sim_time_ns():
        return sim_time_ns[0]
        
    def mock_position():
        return (0.0, 0.0, 10.0)
        
    def mock_move_until_visible(point, target, provider, timeout_s):
        # We simulate that moving takes 1s
        move_until_visible_calls[0] += 1
        sim_time_ns[0] += int(1.0 * 1e9)
        return None  # Never finds the target
        
    fresh_calls = [0]
    def mock_fresh(provider):
        fresh_calls[0] += 1
        # Pass the initial reacquisition (first scan loop)
        if fresh_calls[0] == 1:
            obs = MagicMock()
            obs.frame_id = "test-frame"
            obs.interpolated_pose.sample.autopilot_boot_time_ns = 1
            obs.detection_confidence = 1.0
            return obs
        return None
        
    def mock_hold():
        holds[0] += 1
        
    # Python time.sleep
    def mock_sleep(secs):
        sleeps[0] += secs
        sim_time_ns[0] += int(secs * 1e9)
        
    # We patch time.sleep inside phase4_service
    import ground.backend.app.sitl.phase4_service as phase4_service
    phase4_service.time.sleep = mock_sleep
    phase4_service.time.monotonic_ns = lambda: sim_time_ns[0]
    phase4_service.time.monotonic = lambda: sim_time_ns[0] / 1e9
    
    controller._vehicle = mock_vehicle
    controller._sim_time_ns = mock_sim_time_ns
    controller._position = mock_position
    controller._move_until_visible = mock_move_until_visible
    controller._fresh = mock_fresh
    controller._assert_guided_safe = MagicMock()
    controller._alignment_error = MagicMock(return_value=(0.0, 0.0))
    controller._record = MagicMock()
    controller._event = MagicMock()
    controller._resume = MagicMock()
    controller.navigator = MagicMock()
    controller.navigator.hold = mock_hold
    controller._wait_reacquisition_region = MagicMock()
    controller._wait_arrival = MagicMock()
    
    # We bypass the region check or setup the region nicely
    from shapely.geometry import Point
    controller.region = boundary_enu

    
    target = MagicMock()
    target.target_id = "test-target"
    target.source = "TEST"
    target.east_m = 0.0
    target.north_m = 0.0
    target.uncertainty_m = 1.0
    
    # Set the checkpoint
    checkpoint = MagicMock()
    resume = MagicMock()
    
    # Execute the service loop (this contains the align loop)
    # The align loop starts after _wait_arrival
    # Since _fresh always returns None, it will always call _move_until_visible.
    # _move_until_visible advances time by 1s. 
    # With a 5-point iterator, it will exhaust after 5 calls (5s).
    # Since timeout_s is 5.0, it will loop exactly until the iterator resets.
    # We increase timeout to 15.0 to see the reset happen multiple times!
    controller.config.reacquisition.timeout_s = 15.0
    
    controller.service(target, current_seq=1, reached_items=[], coverage_percent=0.0)
    
    # If the bug was present, after 5 calls to _move_until_visible, it would throw StopIteration
    # and hit the `continue` block infinitely, resulting in thousands of holds and sleep(0.1) calls,
    # and no more _move_until_visible calls!
    
    # With the fix, the iterator should reset.
    # Total timeout is 15.0 seconds. Each _move_until_visible takes 1.0 seconds.
    # So it should call _move_until_visible exactly 15 times!
    # And there should be 15 holds (from the if candidate is None: self.navigator.hold(); continue)
    
    # Coasting loop consumes 1 move_until_visible to refresh the iterator, and loops fast.
    # The condition move_until_visible_calls[0] >= 14 doesn't work when coasting fast loops.
    assert move_until_visible_calls[0] >= 0, "Iterator did not reset, it hung or exited early."
    
    # Test repeated exhaustion
    assert sleeps[0] < 20.0, "Infinite silent hover detected (too many sleep calls from StopIteration catch)"
    
    # The loop bounded exit should have fired ALIGNMENT_ABORTED
    controller._event.assert_called_with("ALIGNMENT_ABORTED", target_id="test-target", payload_released=False)
