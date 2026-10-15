from fastapi.testclient import TestClient


def test_health_and_system_status(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    system = client.get("/api/system/status").json()
    assert system["drone"] == "SIMULATED / NOT CONNECTED"
    assert system["camera"] == "NOT VERIFIED"


def test_create_get_and_list_mission(client: TestClient) -> None:
    created = client.post("/api/missions", json={"name": "Search Alpha"})
    assert created.status_code == 201
    mission = created.json()
    assert mission["state"] == "DRAFT"
    assert client.get(f"/api/missions/{mission['id']}").json()["name"] == "Search Alpha"
    assert len(client.get("/api/missions").json()) == 1


def test_validate_requires_boundary(client: TestClient) -> None:
    mission = client.post("/api/missions", json={"name": "No Boundary"}).json()
    response = client.post(f"/api/missions/{mission['id']}/validate")
    assert response.status_code == 422
    assert "not been imported" in response.json()["detail"]


def test_boundary_upload_limit_and_extension(client: TestClient) -> None:
    mission = client.post("/api/missions", json={"name": "Upload Safety"}).json()
    unsupported = client.post(
        f"/api/missions/{mission['id']}/boundary",
        files={"boundary_file": ("boundary.txt", b"hello", "text/plain")},
    )
    assert unsupported.status_code == 415
    too_large = client.post(
        f"/api/missions/{mission['id']}/boundary",
        files={"boundary_file": ("boundary.geojson", b"x" * (1_048_576 + 1))},
    )
    assert too_large.status_code == 413


def test_boundary_validation_workflow(client: TestClient, mission_id: str) -> None:
    mission = client.post(f"/api/missions/{mission_id}/validate")
    assert mission.status_code == 200
    assert mission.json()["state"] == "VALIDATED"
    events = client.get(f"/api/missions/{mission_id}/events").json()
    assert [event["type"] for event in events] == [
        "MISSION_CREATED", "BOUNDARY_IMPORTED", "MISSION_VALIDATED"
    ]

