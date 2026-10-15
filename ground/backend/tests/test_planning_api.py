from __future__ import annotations

from fastapi.testclient import TestClient


def configured_request() -> dict[str, object]:
    return {
        "search_altitude_m": 15,
        "lane_spacing_m": 10,
        "effective_swath_m": 10,
        "edge_margin_m": 2,
        "search_speed_m_s": 4,
        "sweep_strategy": "AUTO_OPTIMIZED",
        "fixed_sweep_angle_deg": 0,
        "turn_penalty_m": 3,
        "min_planned_coverage_percent": 98,
        "max_waypoints": 500,
    }


def test_plan_requires_validated_boundary(client: TestClient, mission_id: str) -> None:
    response = client.post(f"/api/missions/{mission_id}/plan", json=configured_request())
    assert response.status_code == 422
    assert "validated" in response.json()["detail"]


def test_generate_persist_and_retrieve_plan(client: TestClient, mission_id: str) -> None:
    assert client.post(f"/api/missions/{mission_id}/validate").status_code == 200
    generated = client.post(f"/api/missions/{mission_id}/plan", json=configured_request())
    assert generated.status_code == 200
    plan = generated.json()
    assert plan["validation"]["valid"]
    assert plan["validation"]["planned_coverage_percent"] >= 98
    assert plan["plan_fingerprint"] and len(plan["plan_fingerprint"]) == 64
    assert plan["segments"] and all(segment["points_wgs84"] for segment in plan["segments"])
    assert client.get(f"/api/missions/{mission_id}").json()["state"] == "READY"
    assert client.get(f"/api/missions/{mission_id}/plan").json()["plan_fingerprint"] == plan["plan_fingerprint"]
    assert "COVERAGE_PLAN_CREATED" in [
        event["type"] for event in client.get(f"/api/missions/{mission_id}/events").json()
    ]


def test_engineering_sitl_status_is_guarded_without_opening_connection(client: TestClient) -> None:
    response = client.get("/api/engineering/sitl/status")
    assert response.status_code == 200
    assert response.json()["connection"] == "tcp:127.0.0.1:5760"
