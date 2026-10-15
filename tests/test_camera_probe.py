from tools.camera_probe import classify_camera


def test_camera_detected_only_when_sensor_is_enumerated() -> None:
    commands = [{"stdout": "Available cameras\n0 : ov9281 [1280x800]", "stderr": ""}]
    status, _ = classify_camera(commands, "camera_auto_detect=1", "")
    assert status == "DETECTED"


def test_camera_configuration_is_not_detection() -> None:
    commands = [{"stdout": "No cameras available!", "stderr": ""}]
    status, _ = classify_camera(commands, "dtoverlay=ov9281", "")
    assert status == "CONFIGURATION_PRESENT_BUT_NOT_DETECTED"


def test_camera_driver_probe_failure_is_distinct() -> None:
    commands = [{"stdout": "No cameras available!", "stderr": ""}]
    status, _ = classify_camera(commands, "", "ov9281 probe failed with error -5")
    assert status == "DRIVER_LOADED_BUT_SENSOR_NOT_RESPONDING"

