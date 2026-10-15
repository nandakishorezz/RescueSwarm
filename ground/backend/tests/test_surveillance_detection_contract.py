from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ground.backend.app.services.telemetry_service import RealtimeEnvelope
from ground.backend.app.sitl.surveillance_service import (
    ACTIVE_DETECTION_CONFIDENCE,
    ACTIVE_INFERENCE_SIZE,
    accepts_active_detection,
    allows_survivor_registration,
    brightness_statistics,
    detection_display_label,
    is_persistent_survivor_estimate,
)


def test_active_checkpoint_class_zero_has_explicit_person_display_label() -> None:
    active = Path("data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt")
    assert detection_display_label(active, 0, "item") == "person"
    assert detection_display_label(active, 1, "other") == "other"
    assert detection_display_label(Path("data/another.pt"), 0, "item") == "item"


def test_live_filter_uses_the_accepted_v3_confidence_threshold() -> None:
    assert ACTIVE_DETECTION_CONFIDENCE == 0.25
    assert ACTIVE_INFERENCE_SIZE == 640
    assert accepts_active_detection(0.25)
    assert not accepts_active_detection(0.2499)


def test_camera_detection_payload_is_serializable_and_backward_compatible() -> None:
    payload = {
        "image_b64": "jpeg", "detections": [{"class_id": 0, "class": "item", "display_label": "person", "conf": .9, "bbox": [1, 2, 3, 4]}],
        "frame_id": "camera:1", "detection_image_width": 1280, "detection_image_height": 800,
    }
    envelope = RealtimeEnvelope(type="camera_frame", mission_id="mission", vehicle_id="drone_01", sequence=1, payload=payload)
    assert envelope.model_dump(mode="json")["payload"] == payload


def test_only_confirmed_current_mission_tracks_register_as_survivors() -> None:
    candidate = SimpleNamespace(mission_id="mission-a", state=SimpleNamespace(value="CANDIDATE"))
    confirmed = SimpleNamespace(mission_id="mission-a", state=SimpleNamespace(value="CONFIRMED"))
    stale = SimpleNamespace(mission_id="mission-old", state=SimpleNamespace(value="CONFIRMED"))
    assert not is_persistent_survivor_estimate(candidate, "mission-a")
    assert is_persistent_survivor_estimate(confirmed, "mission-a")
    assert not is_persistent_survivor_estimate(stale, "mission-a")


def test_only_the_real_search_phase_accepts_persistent_survivor_evidence() -> None:
    assert allows_survivor_registration("SEARCHING")
    assert allows_survivor_registration(SimpleNamespace(value="SEARCHING"))
    for state in (None, "TAKING_OFF", "RETURNING", "LANDING", "COMPLETED", "FAILED"):
        assert not allows_survivor_registration(state)


def test_brightness_statistics_measure_pixels_without_transforming_them() -> None:
    image = np.array([[0, 10], [20, 255]], dtype=np.uint8)
    before = image.copy()

    stats = brightness_statistics(image)

    assert stats["min"] == 0
    assert stats["max"] == 255
    assert stats["median"] == 15.0
    assert stats["mean"] == 71.25
    np.testing.assert_array_equal(image, before)
