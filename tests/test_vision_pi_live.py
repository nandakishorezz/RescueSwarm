"""Opt-in physical OV9281 smoke test; ordinary CI always skips it."""

from __future__ import annotations

import os

import pytest

from rescue_core.vision.camera import Picamera2CameraSource
from rescue_core.vision.system_metrics import system_snapshot
from tools.list_camera_modes import list_modes


@pytest.mark.pi
@pytest.mark.camera
@pytest.mark.vision
def test_live_picamera2_enumeration_and_100_frame_capture() -> None:
    if os.getenv("RESCUESWARM_RUN_CAMERA") != "1":
        pytest.skip("physical camera test is opt-in")
    system = system_snapshot()
    assert system["raspberry_pi"], "RESCUESWARM_RUN_CAMERA was requested on a non-Pi host"
    modes = list_modes()
    assert modes["status"] == "PASS", modes["reason"]
    source = Picamera2CameraSource("ov9281_test", 640, 480, 30, "Y8")
    source.start()
    try:
        frames = [source.read() for _ in range(100)]
    finally:
        source.stop()
    assert all(frame is not None for frame in frames)
    assert all(right.sequence == left.sequence + 1 for left, right in zip(frames, frames[1:]))
