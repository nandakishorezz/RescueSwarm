from __future__ import annotations


def test_geolocation_status_is_honest_without_airborne_process(client) -> None:
    response = client.get("/api/system/geolocation-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["ready"] is False
    assert payload["state"] == "CALIBRATION_MISSING"
    assert payload["physical_accuracy"] == "NOT_MEASURED"


def test_hardware_validation_is_read_only_and_honest(client) -> None:
    response = client.get("/api/system/hardware-validation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "PHYSICAL BENCH"
    assert payload["physical_flight"] is False
