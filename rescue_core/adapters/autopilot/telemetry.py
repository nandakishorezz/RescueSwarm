"""Pure MAVLink message parsing and telemetry aggregation."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _valid_scaled(value: Any, divisor: float, invalid: set[int] | None = None) -> float | None:
    if value is None or (invalid and value in invalid):
        return None
    return float(value) / divisor


def firmware_version_from_uint(value: int) -> str:
    """Convert MAVLink flight_sw_version uint32 into an ArduPilot version string."""

    major = (value >> 24) & 0xFF
    minor = (value >> 16) & 0xFF
    patch = (value >> 8) & 0xFF
    release_type = value & 0xFF
    return f"{major}.{minor}.{patch} (type {release_type})"


def message_dict(message: Any) -> dict[str, Any]:
    """Return message fields without pymavlink framing internals."""

    data = dict(message.to_dict())
    data.pop("mavpackettype", None)
    return data


def parse_gps_raw(data: dict[str, Any]) -> dict[str, Any]:
    """Parse GPS_RAW_INT using documented MAVLink scaling and sentinels."""

    eph = _valid_scaled(data.get("eph"), 100.0, {65535})
    return {
        "fix_type": data.get("fix_type"),
        "satellites_visible": None
        if data.get("satellites_visible") in (None, 255)
        else data.get("satellites_visible"),
        "eph": eph,
        "hdop": eph,
        "epv": _valid_scaled(data.get("epv"), 100.0, {65535}),
        "latitude_deg": _valid_scaled(data.get("lat"), 10_000_000.0),
        "longitude_deg": _valid_scaled(data.get("lon"), 10_000_000.0),
        "altitude_msl_m": _valid_scaled(data.get("alt"), 1000.0),
    }


def parse_global_position(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "latitude_deg": _valid_scaled(data.get("lat"), 10_000_000.0),
        "longitude_deg": _valid_scaled(data.get("lon"), 10_000_000.0),
        "altitude_msl_m": _valid_scaled(data.get("alt"), 1000.0),
        "relative_altitude_m": _valid_scaled(data.get("relative_alt"), 1000.0),
        "heading_deg": _valid_scaled(data.get("hdg"), 100.0, {65535}),
    }


def parse_attitude(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "roll_deg": math.degrees(float(data["roll"])) if data.get("roll") is not None else None,
        "pitch_deg": math.degrees(float(data["pitch"])) if data.get("pitch") is not None else None,
        "yaw_deg": math.degrees(float(data["yaw"])) if data.get("yaw") is not None else None,
    }


def parse_sys_status(data: dict[str, Any]) -> dict[str, Any]:
    voltage = data.get("voltage_battery")
    current = data.get("current_battery")
    remaining = data.get("battery_remaining")
    return {
        "voltage_v": None if voltage in (None, 65535) else voltage / 1000.0,
        "current_a": None if current in (None, -1) else current / 100.0,
        "remaining_percent": None if remaining in (None, -1) else remaining,
        "onboard_control_sensors_present": data.get("onboard_control_sensors_present"),
        "onboard_control_sensors_enabled": data.get("onboard_control_sensors_enabled"),
        "onboard_control_sensors_health": data.get("onboard_control_sensors_health"),
    }


class TelemetryAccumulator:
    """Aggregate a passive telemetry window without requesting flight actions."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.latest: dict[str, dict[str, Any]] = {}
        self.heartbeat_times: list[float] = []

    def add(self, message: Any, monotonic_time: float) -> None:
        message_type = message.get_type()
        if message_type == "BAD_DATA":
            return
        self.counts[message_type] += 1
        self.latest[message_type] = message_dict(message)
        if message_type == "HEARTBEAT":
            self.heartbeat_times.append(monotonic_time)

    def heartbeat_rate_hz(self) -> float | None:
        if len(self.heartbeat_times) < 2:
            return None
        duration = self.heartbeat_times[-1] - self.heartbeat_times[0]
        return None if duration <= 0 else (len(self.heartbeat_times) - 1) / duration

    def summary(self) -> dict[str, Any]:
        gps = parse_gps_raw(self.latest["GPS_RAW_INT"]) if "GPS_RAW_INT" in self.latest else None
        position = (
            parse_global_position(self.latest["GLOBAL_POSITION_INT"])
            if "GLOBAL_POSITION_INT" in self.latest
            else None
        )
        attitude = parse_attitude(self.latest["ATTITUDE"]) if "ATTITUDE" in self.latest else None
        battery = parse_sys_status(self.latest["SYS_STATUS"]) if "SYS_STATUS" in self.latest else None
        heading = None
        if position:
            heading = position.get("heading_deg")
        if heading is None and "VFR_HUD" in self.latest:
            heading = self.latest["VFR_HUD"].get("heading")
        return {
            "message_counts": dict(sorted(self.counts.items())),
            "heartbeat_rate_hz": self.heartbeat_rate_hz(),
            "gps": gps,
            "global_position": position,
            "attitude": attitude,
            "heading_deg": heading,
            "battery": battery,
            "ekf_status": self.latest.get("EKF_STATUS_REPORT"),
            "system_status": self.latest.get("SYS_STATUS"),
        }
