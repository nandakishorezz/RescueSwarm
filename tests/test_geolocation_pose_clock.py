from __future__ import annotations

import math

import pytest

from rescue_core.geolocation.clock_sync import ClockSyncEstimator, ClockSyncQuality
from rescue_core.geolocation.pose import PoseHistory
from rescue_core.geolocation.transforms import rotate_vector

from .test_geolocation_helpers import pose_sample, synced_history


def test_clock_sync_converges_and_rejects_high_rtt_and_offset_outlier() -> None:
    estimator = ClockSyncEstimator(minimum_samples=3, maximum_samples=10, max_rtt_ms=20, degraded_rtt_p95_ms=10)
    offset = 2_000_000_000
    for index in range(5):
        sent = 1_000_000_000 + index * 5_000_000
        assert estimator.observe_response(sent, sent + 1_000_000 + offset, sent + 2_000_000)
    assert estimator.offset_ns == pytest.approx(offset, abs=1)
    assert estimator.quality is ClockSyncQuality.SYNCED
    assert not estimator.observe_response(2_000_000_000, 8_000_000_000, 2_100_000_000)
    assert not estimator.observe_response(2_000_000_000, 9_000_000_000, 2_002_000_000)
    assert estimator.rejected_samples == 2


def test_pose_position_and_quaternion_interpolation() -> None:
    history, clock, offset = synced_history()
    frame_host = 2_050_000_000
    result = history.lookup(frame_host, clock)
    assert result.sample.local_position_ned_m == pytest.approx((0.2, 0, -20))
    assert result.diagnostics.interpolation_fraction == pytest.approx(0.5)
    assert result.diagnostics.target_autopilot_time_ns == frame_host + offset
    assert result.diagnostics.clock_sync_quality is ClockSyncQuality.SYNCED


def test_pose_slerp_crosses_yaw_wrap_not_long_way() -> None:
    from rescue_core.geolocation.transforms import quaternion_from_euler

    history = PoseHistory(max_interpolation_gap_ms=500)
    first = pose_sample(1_000_000_000, 1_000_000_000)
    second = pose_sample(1_100_000_000, 1_100_000_000)
    first.quaternion_body_to_ned = tuple(quaternion_from_euler(0, 0, math.radians(359)))
    second.quaternion_body_to_ned = tuple(quaternion_from_euler(0, 0, math.radians(1)))
    history.add(first); history.add(second)
    midpoint = history.lookup(1_050_000_000).sample
    assert rotate_vector(midpoint.quaternion_body_to_ned, [1, 0, 0]) == pytest.approx([1, 0, 0], abs=1e-5)


def test_pose_rejects_empty_large_gap_and_far_extrapolation() -> None:
    empty = PoseHistory()
    with pytest.raises(ValueError, match="history is empty"):
        empty.lookup(1)
    history = PoseHistory(max_interpolation_gap_ms=50, max_extrapolation_ms=10)
    history.add(pose_sample(1_000_000_000, 1_000_000_000))
    history.add(pose_sample(1_100_000_000, 1_100_000_000))
    with pytest.raises(ValueError, match="interpolation gap"):
        history.lookup(1_050_000_000)
    with pytest.raises(ValueError, match="follows history"):
        history.lookup(1_200_000_000)


def test_pose_history_is_bounded_and_rejects_out_of_order() -> None:
    history = PoseHistory(history_seconds=0.2)
    for index in range(10):
        value = 1_000_000_000 + index * 100_000_000
        history.add(pose_sample(value, value))
    assert len(history.samples) <= 3
    history.add(pose_sample(history.samples[-1].autopilot_boot_time_ns, 3_000_000_000))
    assert history.stats.rejected_out_of_order == 1


def test_source_time_pose_lookup_has_no_receipt_time_fallback() -> None:
    history, clock, offset = synced_history()
    result = history.lookup_autopilot_time(2_050_000_000 + offset, clock_sync_quality=clock.quality)
    assert result.sample.local_position_ned_m == pytest.approx((0.2, 0, -20))
    with pytest.raises(ValueError, match="CLOCK_MAPPING_INVALID"):
        history.lookup_autopilot_time(2_050_000_000 + offset, clock_sync_quality=ClockSyncQuality.WARMING_UP)
