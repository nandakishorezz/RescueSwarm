def test_vision_status_is_honest_when_no_pi_link_exists(client) -> None:
    response = client.get("/api/engineering/vision/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["source"] == "NOT_CONNECTED"
    assert payload["physical_camera_verified"] is False
    assert payload["capture_fps"] is None
