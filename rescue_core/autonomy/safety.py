"""Continuous GUIDED supervision and fail-closed adapter authority checks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from shapely.geometry import Point, Polygon


class RescueSafetyError(PermissionError):
    pass


def require_sitl_command_adapter(adapter: Any) -> None:
    """Reject duck-typed Stage 1/6 physical adapters before orchestration starts."""
    cls = type(adapter)
    if cls.__name__ != "SITLAutopilotAdapter" or cls.__module__ != "ground.backend.app.sitl.adapter":
        raise RescueSafetyError("Stage 7S command orchestration requires the isolated SITLAutopilotAdapter")
    connection = getattr(adapter, "connection_string", "")
    if not connection.startswith(("tcp:127.0.0.1:", "udp:127.0.0.1:", "udpin:127.0.0.1:", "udpout:127.0.0.1:")):
        raise RescueSafetyError("Stage 7S adapter endpoint is not an explicit IPv4 loopback SITL endpoint")


@dataclass(frozen=True)
class GuidedSafetySnapshot:
    link_alive: bool
    armed: bool
    flight_mode: str
    east_m: float
    north_m: float
    altitude_m: float
    payload_valid: bool


class GuidedSafetySupervisor:
    def __init__(
        self, region: Polygon, command_timeout_s: float,
        minimum_altitude_m: float = 0.5, maximum_altitude_m: float = 30,
        buffer_m: float = 1e-5,
    ):
        self.region = region
        self.command_timeout_s = command_timeout_s
        self.minimum_altitude_m = minimum_altitude_m
        self.maximum_altitude_m = maximum_altitude_m
        self.buffer_m = buffer_m
        self.last_guided_command_monotonic: float | None = None

    def record_command(self, now: float | None = None) -> None:
        self.last_guided_command_monotonic = now if now is not None else time.monotonic()

    def check(self, snapshot: GuidedSafetySnapshot, now: float | None = None) -> list[str]:
        current = now if now is not None else time.monotonic()
        failures: list[str] = []
        if not snapshot.link_alive:
            failures.append("MAVLINK_STALE")
        if not snapshot.armed:
            failures.append("VEHICLE_DISARMED")
        if snapshot.flight_mode != "GUIDED":
            failures.append("UNEXPECTED_MODE")
        if not self.region.buffer(self.buffer_m).covers(Point(snapshot.east_m, snapshot.north_m)):
            failures.append("OUTSIDE_OPERATIONAL_REGION")
        if not self.minimum_altitude_m <= snapshot.altitude_m <= self.maximum_altitude_m:
            failures.append("ALTITUDE_OUT_OF_BOUNDS")
        if not snapshot.payload_valid:
            failures.append("PAYLOAD_STATE_INVALID")
        if self.last_guided_command_monotonic is None or current - self.last_guided_command_monotonic > self.command_timeout_s:
            failures.append("GUIDED_COMMAND_WATCHDOG")
        return failures
