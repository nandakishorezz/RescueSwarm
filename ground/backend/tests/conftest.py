from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ground.backend.app.config import GroundConfig, load_ground_config
from ground.backend.app.main import create_app


@pytest.fixture
def ground_config() -> GroundConfig:
    base = load_ground_config("config/ground.yaml")
    return base.model_copy(
        update={
            "database": base.database.model_copy(update={"url": "sqlite:///:memory:"}),
            "simulator": base.simulator.model_copy(
                update={"update_interval_seconds": 0.01, "total_steps": 12}
            ),
        }
    )


@pytest.fixture
def client(ground_config: GroundConfig):
    with TestClient(create_app(ground_config)) as test_client:
        yield test_client


@pytest.fixture
def valid_geojson() -> bytes:
    return json.dumps(
        {
            "type": "Polygon",
            "coordinates": [[
                [77.5900, 12.9700],
                [77.5910, 12.9700],
                [77.5910, 12.9710],
                [77.5900, 12.9710],
                [77.5900, 12.9700],
            ]],
        }
    ).encode()


@pytest.fixture
def mission_id(client: TestClient, valid_geojson: bytes) -> str:
    mission = client.post("/api/missions", json={"name": "Deterministic Test"}).json()
    response = client.post(
        f"/api/missions/{mission['id']}/boundary",
        files={"boundary_file": ("boundary.geojson", valid_geojson, "application/geo+json")},
    )
    assert response.status_code == 200
    return mission["id"]

