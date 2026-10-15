from unittest.mock import MagicMock
from pathlib import Path
from shapely.geometry import Polygon
from ground.backend.app.sitl.phase4_service import Phase4TargetServiceController
from rescue_core.autonomy.config import load_rescue_config
config = load_rescue_config()
config.reacquisition.timeout_s = 15.0
config.guided.setpoint_rate_hz = 10
config.reacquisition.search_radius_m = 2.0
class MockAdapter: connection_string = "tcp:127.0.0.1:5760"
MockAdapter.__name__ = "SITLAutopilotAdapter"
MockAdapter.__module__ = "ground.backend.app.sitl.adapter"
boundary_enu = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
controller = Phase4TargetServiceController(
    adapter=MockAdapter(), pipeline=MagicMock(), config=config, frame=MagicMock(),
    boundary_enu=boundary_enu, mission_id="test-mission", mission_fingerprint="test-fingerprint",
    search_first_seq=1, rtl_seq=10, run_dir=Path("."), payload=MagicMock(), inventory=MagicMock()
)
sim_time_ns = [10 * 1_000_000_000]
def mock_vehicle(): return ("GUIDED", True, 0.0, sim_time_ns[0])
controller._vehicle = mock_vehicle
controller._sim_time_ns = lambda: sim_time_ns[0]
controller._position = lambda: (0.0, 0.0, 10.0)
calls = []
def mock_move(p, t, pr, to):
    calls.append(1)
    sim_time_ns[0] += int(1.0 * 1e9)
    print("Move! Time is now", sim_time_ns[0]/1e9)
    return None
controller._move_until_visible = mock_move
fresh_calls = [0]
def mock_fresh(p):
    fresh_calls[0] += 1
    if fresh_calls[0] == 1:
        obs = MagicMock(); obs.frame_id = "test-frame"
        return obs
    return None
controller._fresh = mock_fresh
import ground.backend.app.sitl.phase4_service as p4s
def mock_sleep(s): sim_time_ns[0] += int(s*1e9)
p4s.time.sleep = mock_sleep
controller._assert_guided_safe = MagicMock()
controller._alignment_error = MagicMock(return_value=(0.0, 0.0))
controller._record = MagicMock()
controller._event = MagicMock()
controller._resume = MagicMock()
controller.navigator = MagicMock()
controller._wait_reacquisition_region = MagicMock()
controller._wait_arrival = MagicMock()
controller.region = boundary_enu
target = MagicMock(); target.target_id = "test-target"; target.east_m = 0.0; target.north_m = 0.0; target.uncertainty_m = 1.0
print("Timeout before:", controller.config.reacquisition.timeout_s)
controller.service(target, current_seq=1, reached_items=[], coverage_percent=0.0)
print("Move calls:", len(calls))
print("Time after:", sim_time_ns[0]/1e9)
