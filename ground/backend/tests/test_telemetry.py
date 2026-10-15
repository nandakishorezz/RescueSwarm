from ground.backend.app.domain.vehicle import LinkState
from ground.backend.app.services.telemetry_service import classify_link_age


def test_stale_telemetry_thresholds() -> None:
    assert classify_link_age(1.99) is LinkState.CONNECTED
    assert classify_link_age(2.0) is LinkState.DEGRADED
    assert classify_link_age(5.0) is LinkState.DEGRADED
    assert classify_link_age(5.01) is LinkState.STALE

