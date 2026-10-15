from fastapi.testclient import TestClient


def test_digital_twin_snapshot_is_read_only_and_truth_labeled(client: TestClient, mission_id: str) -> None:
    response = client.get(f"/api/missions/{mission_id}/digital-twin")
    assert response.status_code == 200
    payload = response.json()
    assert payload["hardware_gate_h1"] == "PENDING"
    assert payload["ground"]["provider"] == "FLAT"
    assert payload["camera"]["calibration"]["evidence_class"] == "SYNTHETIC"
    assert payload["camera"]["extrinsics"]["evidence_class"] == "SYNTHETIC"
    assert payload["vehicle_geometry"]["evidence_class"] == "CONFIGURED_ASSUMPTION"
    assert payload["launch_landing_zone"]["physical_fit_verified"] is False


def test_replay_empty_state_is_deterministic(client: TestClient, mission_id: str) -> None:
    response = client.get(f"/api/missions/{mission_id}/replay")
    assert response.status_code == 200
    payload = response.json()
    assert payload["deterministic"] is True
    assert payload["samples"] == []
    assert payload["source"] == "SIMULATED"


def test_artifact_endpoint_is_allowlisted(client: TestClient, mission_id: str) -> None:
    listing = client.get(f"/api/missions/{mission_id}/artifacts")
    assert listing.status_code == 200
    assert all(item["name"].endswith(".md") for item in listing.json()["artifacts"])
    assert client.get("/api/artifacts/requirements-lock.txt").status_code == 404
    assert client.get("/api/artifacts/..%2Fconfig%2Fground.yaml").status_code in {404, 422}

