from pathlib import Path

import pytest
from pydantic import ValidationError

from rescue_core.common.config import load_config


def test_prototype_config_loads() -> None:
    config = load_config(Path("config/prototype.yaml"))
    assert config.autopilot.type == "ardupilot"
    assert config.autopilot.baud == 57600
    assert config.vehicle.fleet_size == 1
    assert config.network.external_internet_required is False


def test_invalid_fleet_size_is_rejected(tmp_path: Path) -> None:
    original = Path("config/prototype.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text(original.replace("fleet_size: 1", "fleet_size: 0"), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(bad)

