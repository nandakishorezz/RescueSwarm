from __future__ import annotations

import time

import numpy as np

from rescue_core.simulation.perception import SensorRealismCameraSource
from rescue_core.simulation.sensor_realism import (
    SensorProfileId,
    get_sensor_realism_profile,
    sensor_realism_profiles,
)
from rescue_core.vision.camera import CameraSource
from rescue_core.vision.models import CameraMetadata, FramePacket


class _Frames(CameraSource):
    def __init__(self, count: int = 5) -> None:
        super().__init__("phase3-test")
        self.count = count
        self.running = False

    def start(self) -> None:
        self.running = True

    def read(self) -> FramePacket | None:
        if not self.running or self.sequence >= self.count:
            return None
        sequence = self.sequence
        return self._packet(
            np.full((10, 12), 100 + sequence, dtype=np.uint8), "MONO8",
            CameraMetadata(sensor_timestamp_ns=1_000_000_000 + sequence * 10_000_000),
            capture_monotonic_ns=time.monotonic_ns(),
            capture_wall_ns=time.time_ns(),
            capture_sim_ns=1_000_000_000 + sequence * 10_000_000,
        )

    def stop(self) -> None:
        self.running = False


def test_phase3_profiles_are_versioned_bounded_and_measurement_layer() -> None:
    profiles = sensor_realism_profiles()
    assert set(profiles) == set(SensorProfileId)
    for profile in profiles.values():
        assert profile.schema_version == "3.0"
        assert profile.physical_calibration is False
        assert profile.camera.max_pending_frames <= 64
        assert profile.camera.rolling_shutter_model == "NOT_APPLICABLE_TO_REFERENCE_CAMERA"
        parameters = profile.ardupilot_sensor_parameters()
        assert parameters
        assert all(name.startswith("SIM_") for name in parameters)
        assert not any(token in name for name in parameters for token in ("EKF", "TARGET"))
        assert "position" not in profile.model_dump_json().lower()


def test_camera_latency_preserves_capture_time_order_and_is_bounded() -> None:
    profile = get_sensor_realism_profile("REFERENCE_NOMINAL")
    profile.camera.latency_ms = 8
    profile.camera.jitter_ms = 2
    profile.camera.max_pending_frames = 8
    source = SensorRealismCameraSource(_Frames(), profile.camera, sensor_seed=42)
    source.start()
    packets = []
    while (packet := source.read()) is not None:
        packets.append(packet)
    source.stop()
    assert [packet.sequence for packet in packets] == list(range(5))
    assert [packet.capture_timestamp_sim_ns for packet in packets] == [
        1_000_000_000 + index * 10_000_000 for index in range(5)
    ]
    assert all(packet.camera_metadata.extra["capture_timestamp_preserved"] for packet in packets)
    assert all(packet.camera_metadata.extra["host_time_authoritative_for_pose"] is False for packet in packets)
    assert source.metrics.frames_released == 5
    assert source.metrics.frames_dropped == 0
    assert source.metrics.max_pending_delayed_frames <= 8
    assert source.metrics.out_of_order_releases == 0


def test_camera_noise_is_seeded_and_mono_only() -> None:
    profile = get_sensor_realism_profile("REFERENCE_CHALLENGING")

    def collect() -> list[np.ndarray]:
        source = SensorRealismCameraSource(_Frames(2), profile.camera, sensor_seed=77)
        source.start()
        result = []
        while (packet := source.read()) is not None:
            assert packet.pixel_format == "MONO8"
            assert packet.image.ndim == 2
            result.append(packet.image.copy())
        source.stop()
        return result

    first, second = collect(), collect()
    assert all(np.array_equal(a, b) for a, b in zip(first, second, strict=True))
    assert not np.array_equal(first[0], np.full((10, 12), 100, dtype=np.uint8))
