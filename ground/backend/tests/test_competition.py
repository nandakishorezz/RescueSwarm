from fastapi.testclient import TestClient

from ground.backend.app.config import GroundConfig
from ground.backend.app.main import create_app


def test_competition_mode_disables_simulator_api(ground_config: GroundConfig) -> None:
    competition = ground_config.model_copy(
        update={
            "competition": ground_config.competition.model_copy(update={"enabled": True})
        }
    )
    with TestClient(create_app(competition)) as client:
        response = client.post("/api/engineering/simulator/missing/start")
        assert response.status_code == 403
        assert client.get("/api/engineering/sitl/status").status_code == 403
        assert client.post("/api/engineering/sitl/missions/missing/execute").status_code == 403
        assert client.get("/api/engineering/vision/status").status_code == 403
