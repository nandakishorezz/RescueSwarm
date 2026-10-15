from __future__ import annotations

import time

import numpy as np
import pytest

from rescue_core.vision.health import ComponentHealth, HealthState
from rescue_core.vision.models import Detection, DetectionResult, FramePacket


def frame(sequence: int = 0, width: int = 8, height: int = 6) -> FramePacket:
    return FramePacket(
        frame_id=f"test:{sequence}", source_id="test", capture_timestamp_monotonic_ns=time.monotonic_ns(),
        capture_timestamp_wall_ns=time.time_ns(), sequence=sequence, width=width, height=height,
        pixel_format="Y8", image=np.zeros((height, width), dtype=np.uint8),
    )


def test_frame_packet_has_stable_identity_and_high_resolution_timestamps() -> None:
    first = frame(1)
    time.sleep(0.001)
    second = frame(2)
    assert first.frame_id != second.frame_id
    assert second.capture_timestamp_monotonic_ns > first.capture_timestamp_monotonic_ns
    assert first.metadata_dict()["pixel_format"] == "Y8"


def test_frame_packet_rejects_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        FramePacket("bad", "test", 1, 1, 0, 4, 4, "Y8", np.zeros((3, 4), dtype=np.uint8))


def test_detection_reports_pixel_size_center_bottom_center_and_normalized_box() -> None:
    detection = Detection(0, "person", 0.75, 20, 10, 60, 90, 100, 100)
    value = detection.as_dict()
    assert value["bbox_center_pixels"] == [40, 50]
    assert value["bbox_bottom_center_pixels"] == [40, 90]
    assert value["bbox_width_px"] == 40
    assert value["bbox_area_percent"] == 32
    assert value["bbox_normalized"] == [0.2, 0.1, 0.6, 0.9]


def test_detection_result_preserves_capture_timestamp() -> None:
    packet = frame()
    started = time.monotonic_ns()
    finished = time.monotonic_ns()
    result = DetectionResult(
        packet.frame_id, packet.source_id, packet.capture_timestamp_monotonic_ns,
        packet.capture_timestamp_wall_ns, started, finished, "mock", "mock", "Y8",
        "RGB_FLOAT32_CHW", [], 1, 2, 1, (finished - packet.capture_timestamp_monotonic_ns) / 1e6,
    )
    assert result.as_dict()["capture_timestamp_monotonic_ns"] == packet.capture_timestamp_monotonic_ns


def test_health_transitions_and_stale_timeout() -> None:
    health = ComponentHealth()
    health.success(1_000_000_000)
    assert health.state is HealthState.READY
    assert health.evaluate_timeout(1, 2_100_000_000) is HealthState.FAILED
    health = ComponentHealth()
    health.error("one")
    assert health.state is HealthState.DEGRADED
    health.error("two")
    health.error("three")
    assert health.state is HealthState.FAILED
