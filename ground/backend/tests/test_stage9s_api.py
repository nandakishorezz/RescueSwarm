from fastapi.testclient import TestClient

from ground.backend.app.main import create_app


def request_config() -> dict[str, object]:
    return {
        "search_altitude_m": 15, "lane_spacing_m": 10, "effective_swath_m": 10,
        "edge_margin_m": 2, "search_speed_m_s": 4, "sweep_strategy": "AUTO_OPTIMIZED",
        "fixed_sweep_angle_deg": 0, "turn_penalty_m": 3,
        "min_planned_coverage_percent": 98, "max_waypoints": 500,
    }


def prepare(client: TestClient, mission_id: str) -> None:
    assert client.post(f"/api/missions/{mission_id}/validate").status_code == 200
    assert client.post(f"/api/missions/{mission_id}/plan", json=request_config()).status_code == 200


def test_one_global_plan_creates_one_fleet_read_model(client: TestClient, mission_id: str):
    prepare(client, mission_id)
    created = client.post(f"/api/engineering/fleet/missions/{mission_id}/plan", json={"fleet_size": 2})
    assert created.status_code == 200
    plan = created.json()
    assert plan["fleet_size"] == 2
    assert plan["hardware_gate_h1"] == "PENDING"
    assert len(plan["assignments"]) == 2
    status = client.get(f"/api/missions/{mission_id}/fleet").json()
    assert status["vehicle_ids"] == ["drone_01", "drone_02"]
    assert status["video"] == {"drone_01": "SIMULATED", "drone_02": "SIMULATED"}
    assert client.get(f"/api/missions/{mission_id}/fleet/plan").status_code == 200
    assert client.get(f"/api/missions/{mission_id}/fleet/vehicles").json() == []


def test_fleet_engineering_plan_is_disabled_in_competition(ground_config, mission_id, valid_geojson):
    competition = ground_config.model_copy(update={
        "competition": ground_config.competition.model_copy(update={"enabled": True}),
    })
    with TestClient(create_app(competition)) as client:
        mission = client.post("/api/missions", json={"name": "Competition fleet"}).json()
        client.post(
            f"/api/missions/{mission['id']}/boundary",
            files={"boundary_file": ("boundary.geojson", valid_geojson, "application/geo+json")},
        )
        prepare(client, mission["id"])
        response = client.post(f"/api/engineering/fleet/missions/{mission['id']}/plan", json={"fleet_size": 2})
        assert response.status_code == 403


def test_system_status_keeps_h1_pending_and_marks_fleet_sitl_only(client: TestClient):
    status = client.get("/api/system/status").json()
    assert status["hardware_gate_h1"]["state"] == "PENDING"
    assert status["stage_9s"] == "MULTI_ARDUCOPTER_SITL_ONLY"
    assert status["fleet"]["physical_multi_drone"] == "PENDING"
