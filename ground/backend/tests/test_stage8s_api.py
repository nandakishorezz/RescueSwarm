from fastapi.testclient import TestClient

from ground.backend.app.main import create_app


def test_resilience_status_keeps_h1_pending(client, mission_id):
    response = client.get(f"/api/system/resilience?mission_id={mission_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["hardware_gate_h1"]["state"] == "PENDING"
    assert body["competition_readiness"]["overall"] == "NOT_COMPETITION_READY"
    assert body["failure_injection_enabled"] is True


def test_engineering_scenario_catalog_and_execution(client):
    scenarios = client.get("/api/engineering/failures/scenarios")
    assert scenarios.status_code == 200
    identifiers = {item["scenario_id"] for item in scenarios.json()}
    assert "mavlink_loss_guided" in identifiers
    result = client.post("/api/engineering/failures/scenarios/mavlink_loss_guided/run")
    assert result.status_code == 200
    assert result.json()["passed"] is True
    assert result.json()["drop_prevented"] is True


def test_forbidden_operator_actions_are_rejected_and_audited(client, mission_id):
    for action in ("MANUAL_WAYPOINT", "MANUAL_GOTO", "MANUAL_TARGET_CONFIRM", "MANUAL_PAYLOAD_DROP", "MANUAL_FLIGHT_MODE"):
        response = client.post(
            f"/api/operator/missions/{mission_id}/actions/{action}",
            json={"mission_state": "RUNNING"},
        )
        assert response.status_code == 403
    events = client.get(f"/api/missions/{mission_id}/events").json()
    rejected = [item for item in events if item["type"] == "OPERATOR_ACTION" and not item["metadata"]["allowed"]]
    assert len(rejected) == 5


def test_competition_mode_disables_all_failure_apis(ground_config):
    competition = ground_config.model_copy(update={
        "application": ground_config.application.model_copy(update={"mode": "competition"}),
        "competition": ground_config.competition.model_copy(update={"enabled": True}),
    })
    with TestClient(create_app(competition)) as client:
        assert client.get("/api/engineering/failures/scenarios").status_code == 403
        assert client.post("/api/engineering/failures/scenarios/browser_loss_auto/run").status_code == 403
        status = client.get("/api/system/resilience").json()
        assert status["competition_locked"] is True
        assert status["failure_injection_enabled"] is False


def test_websocket_reconnect_snapshot_contains_authoritative_state(client, mission_id):
    with client.websocket_connect(f"/ws/mission/{mission_id}") as socket:
        first = socket.receive_json()
    with client.websocket_connect(f"/ws/mission/{mission_id}") as socket:
        recovered = socket.receive_json()
    assert recovered["type"] == "system.snapshot"
    assert recovered["payload"]["mission"]["id"] == mission_id
    assert isinstance(recovered["payload"]["vehicles"], list)
    assert recovered["payload"]["resilience"]["hardware_gate_h1"]["state"] == "PENDING"
    assert recovered["payload"]["snapshot"] is True
