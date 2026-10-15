from fastapi.testclient import TestClient


def test_websocket_connects_with_one_envelope(client: TestClient, mission_id: str) -> None:
    with client.websocket_connect(f"/ws/mission/{mission_id}") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "system.snapshot"
        assert message["mission_id"] == mission_id
        assert message["sequence"] == 0
        assert "vehicle_id" in message
        assert message["payload"]["snapshot"] is True
        assert message["payload"]["resilience"]["hardware_gate_h1"]["state"] == "PENDING"


def test_websocket_receives_simulator_telemetry(client: TestClient, mission_id: str) -> None:
    with client.websocket_connect(f"/ws/mission/{mission_id}") as websocket:
        websocket.receive_json()
        response = client.post(f"/api/engineering/simulator/{mission_id}/start")
        assert response.status_code == 200
        message = websocket.receive_json()
        assert message["type"] == "vehicle.telemetry"
        assert message["vehicle_id"] == "drone_01"
        assert message["payload"]["simulated"] is True
