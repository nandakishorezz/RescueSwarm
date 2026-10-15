import pytest
import time
import math
from unittest.mock import Mock, MagicMock, patch
from ground.backend.app.sitl.phase4_service import Phase4TargetServiceController, ApproachPoint

class MockNavigator:
    def face_local(self, *args): pass
    def hold(self): pass

def setup_controller():
    ctrl = MagicMock()
    ctrl.navigator = MockNavigator()
    ctrl._sim_time_ns = Mock(return_value=1000)
    ctrl._send_goto = Mock()
    ctrl._assert_guided_safe = Mock()
    ctrl._record = Mock()
    return ctrl

def test_a_iterator_exhaustion():
    # Tested natively in test_phase4_iterator.py
    pass

def test_c_early_arrival():
    ctrl = setup_controller()
    ctrl._position = Mock(return_value=(10.0, 10.0, 5.0))
    point = ApproachPoint(east_m=10.5, north_m=10.5, altitude_m=5.0)
    target = MagicMock()
    provider = MagicMock()
    ctrl._fresh = Mock(return_value=None)
    
    result = Phase4TargetServiceController._move_until_visible(ctrl, point, target, provider, 8.0)
    assert result is None
    ctrl._send_goto.assert_called_once()

def test_d_not_yet_arrived():
    ctrl = setup_controller()
    ctrl._position = Mock(return_value=(0.0, 0.0, 5.0))
    point = ApproachPoint(east_m=10.5, north_m=10.5, altitude_m=5.0)
    target = MagicMock()
    provider = MagicMock()
    ctrl._fresh = Mock(return_value=None)
    
    time_values = [1000, 1000, 1000 + int(8.0 * 1e9) + 10]
    ctrl._sim_time_ns = Mock(side_effect=lambda: time_values.pop(0) if time_values else 1e12)
    with patch('time.monotonic', return_value=10.0):
        result = Phase4TargetServiceController._move_until_visible(ctrl, point, target, provider, 8.0)
    assert result is None
    assert ctrl._send_goto.call_count >= 1

def test_e_target_reacquired():
    ctrl = setup_controller()
    ctrl._position = Mock(return_value=(0.0, 0.0, 5.0))
    point = ApproachPoint(east_m=10.5, north_m=10.5, altitude_m=5.0)
    target = MagicMock()
    provider = MagicMock()
    observation = MagicMock()
    ctrl._fresh = Mock(return_value=observation)
    
    result = Phase4TargetServiceController._move_until_visible(ctrl, point, target, provider, 8.0)
    assert result is observation
