from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
import yaml

from rescue_core.geolocation.calibration import CalibrationRegistry, ExtrinsicsQuality
from rescue_core.geolocation.calibration_workflow import detect_checkerboards
from rescue_core.geolocation.config import load_geolocation_config
from rescue_core.geolocation.worker import GeolocationWorker

from .test_geolocation_helpers import nadir_extrinsics, synthetic_calibration


def test_configuration_and_calibration_hash_registry(tmp_path) -> None:
    config = load_geolocation_config()
    assert config.geolocation.calibration_id is None
    assert config.geolocation.worker.queue_size == 32
    registry = CalibrationRegistry(tmp_path)
    calibration, extrinsics = synthetic_calibration(), nadir_extrinsics()
    directory = registry.save(calibration, extrinsics, {"verdict": "PASS", "synthetic": True})
    assert registry.load_intrinsics(calibration.calibration_id).sha256 == calibration.sha256
    assert registry.load_extrinsics(calibration.calibration_id).quality is ExtrinsicsQuality.CALIBRATED
    payload = yaml.safe_load((directory / "intrinsics.yaml").read_text())
    payload["camera_matrix"][0][0] += 1
    (directory / "intrinsics.yaml").write_text(yaml.safe_dump(payload))
    with pytest.raises(ValueError, match="hash mismatch"):
        registry.load_intrinsics(calibration.calibration_id)


def test_calibration_rejects_resolution_or_sensor_mode_mismatch() -> None:
    calibration = synthetic_calibration()
    calibration.assert_compatible(1280, 800, "SYNTHETIC_1280x800")
    with pytest.raises(ValueError, match="CALIBRATION_MISMATCH"):
        calibration.assert_compatible(640, 480, "OTHER")


def test_bounded_geolocation_worker_drops_oldest() -> None:
    handled: list[int] = []
    worker = GeolocationWorker(handled.append, queue_size=2)
    worker.submit(1); worker.submit(2); worker.submit(3)
    assert worker.dropped == 1
    assert [worker.queue.get_nowait(), worker.queue.get_nowait()] == [2, 3]


def test_checkerboard_detection_rejects_duplicate_and_non_pattern(tmp_path) -> None:
    image = np.full((600, 800), 255, dtype=np.uint8)
    square = 50
    for row in range(7):
        for column in range(10):
            if (row + column) % 2 == 0:
                image[100 + row * square:100 + (row + 1) * square,
                      150 + column * square:150 + (column + 1) * square] = 0
    first, duplicate, blank = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    cv2.imwrite(str(first), image); cv2.imwrite(str(duplicate), image); cv2.imwrite(str(blank), np.full_like(image, 255))
    accepted, rejected, size = detect_checkerboards([first, duplicate, blank], (9, 6), 0.025)
    assert size == (800, 600)
    assert len(accepted) == 1
    assert {item["reason"] for item in rejected} == {"NEAR_DUPLICATE_PATTERN_POSE", "BLURRED_FRAME"}
