from pathlib import Path

import pytest

from ground.backend.app.config import GroundConfig
from ground.backend.app.services.offline_guard import OfflineGuard


def test_offline_guard_ready(ground_config: GroundConfig) -> None:
    status = OfflineGuard(ground_config, Path.cwd()).status(database_health=True)
    assert status["status"] == "READY"
    assert status["external_tile_sources"] == 0
    assert status["external_api_dependencies"] == 0
    assert status["local_websocket"] == "OK"


def test_offline_guard_rejects_external_configuration(ground_config: GroundConfig) -> None:
    bad = ground_config.model_copy(
        update={
            "map": ground_config.map.model_copy(
                update={"external_tile_sources": ["remote-tile-source.example"]}
            )
        }
    )
    status = OfflineGuard(bad, Path.cwd()).status(database_health=True)
    assert status["status"] == "FAILED"
    assert "External tile sources" in status["violations"][0]


def test_network_lockdown_is_never_automatic(ground_config: GroundConfig) -> None:
    guard = OfflineGuard(ground_config, Path.cwd())
    with pytest.raises(NotImplementedError, match="never modifies"):
        guard.enable_competition_network_lockdown()

