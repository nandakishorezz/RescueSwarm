from __future__ import annotations

import pytest

from ground.backend.app.sitl.guard import SITLSafetyError, SITLSafetyGuard


@pytest.fixture
def guard() -> SITLSafetyGuard:
    return SITLSafetyGuard(environment="sitl", command_authority="simulation_only")


@pytest.mark.parametrize(
    "connection",
    [
        "/dev/serial0",
        "/dev/ttyAMA0",
        "/dev/ttyACM0",
        "/dev/ttyUSB0",
        "COM3",
        "COM7",
        "tcp:8.8.8.8:5760",
        "udp:192.168.1.20:14550",
        "tcp:0.0.0.0:5760",
        "tcp:localhost:5760",
    ],
)
def test_stage3_commands_reject_hardware_and_nonlocal_connections(
    guard: SITLSafetyGuard, connection: str
) -> None:
    with pytest.raises(SITLSafetyError):
        guard.validate(connection)


@pytest.mark.parametrize(
    "connection",
    ["tcp:127.0.0.1:5760", "udp:127.0.0.1:14550", "udpin:127.0.0.1:14550", "tcp:[::1]:5760"],
)
def test_explicit_loopback_sitl_connections_are_accepted(guard: SITLSafetyGuard, connection: str) -> None:
    assert guard.validate(connection).port > 0


def test_authority_labels_fail_closed() -> None:
    with pytest.raises(SITLSafetyError, match="environment"):
        SITLSafetyGuard("development", "simulation_only").validate("tcp:127.0.0.1:5760")
    with pytest.raises(SITLSafetyError, match="command_authority"):
        SITLSafetyGuard("sitl", "all").validate("tcp:127.0.0.1:5760")
