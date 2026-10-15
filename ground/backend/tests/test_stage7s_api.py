from __future__ import annotations

from fastapi.testclient import TestClient

from ground.backend.app.main import create_app


def test_system_keeps_hardware_gate_h1_pending(client):
    response = client.get("/api/system/status")
    assert response.status_code == 200
    gate = response.json()["hardware_gate_h1"]
    assert gate["gate_id"] == "H1_PHYSICAL_PROTOTYPE"
    assert gate["state"] == "PENDING"


def test_rescue_api_is_competition_protected(ground_config):
    competition = ground_config.model_copy(update={
        "competition": ground_config.competition.model_copy(update={"enabled": True}),
    })
    with TestClient(create_app(competition)) as competition_client:
        response = competition_client.post(
            "/api/engineering/sitl/missions/anything/rescue/execute",
            json={"true_east_m": 1, "true_north_m": 1},
        )
        assert response.status_code == 403


def test_rescue_api_validates_synthetic_scenario_before_execution(client):
    response = client.post(
        "/api/engineering/sitl/missions/anything/rescue/execute",
        json={"true_east_m": 1, "true_north_m": 1, "detection_probability": 2},
    )
    assert response.status_code == 422
