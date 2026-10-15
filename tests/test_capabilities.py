import pytest
from pydantic import ValidationError

from rescue_core.common.capabilities import CapabilityState, VehicleCapabilities
from rescue_core.common.config import load_config
from rescue_core.common.models import EvidenceStatus


def test_configured_is_not_verified() -> None:
    config = load_config("config/prototype.yaml")
    capabilities = VehicleCapabilities.from_prototype_config(config.model_dump())
    assert capabilities.autopilot.configured == "ardupilot"
    assert capabilities.autopilot.status is EvidenceStatus.CONFIGURED
    assert capabilities.camera.status is EvidenceStatus.CONFIGURED
    assert capabilities.payload.status is EvidenceStatus.UNKNOWN
    assert capabilities.fleet_size == 1


def test_verified_capability_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="require evidence"):
        CapabilityState(configured="ov9281", status=EvidenceStatus.VERIFIED)


def test_verified_capability_accepts_evidence() -> None:
    state = CapabilityState(
        configured="ov9281",
        detected_value="ov9281",
        status=EvidenceStatus.VERIFIED,
        evidence="rpicam-hello enumerated sensor",
    )
    assert state.status is EvidenceStatus.VERIFIED

