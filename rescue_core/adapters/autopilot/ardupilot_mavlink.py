"""Read-only pymavlink implementation for ArduCopter diagnostics."""

from __future__ import annotations

import time
from typing import Any

from pymavlink import mavutil

from .base import AutopilotAdapter
from .telemetry import (
    firmware_version_from_uint,
    message_dict,
    parse_attitude,
    parse_global_position,
    parse_gps_raw,
    parse_sys_status,
)


class ArduPilotMavlinkAdapter(AutopilotAdapter):
    """Minimal, synchronous, read-only adapter for a pymavlink connection."""

    def __init__(self, connection: str, baud: int = 57600, heartbeat_timeout: float = 30.0):
        self.connection_string = connection
        self.baud = baud
        self.heartbeat_timeout = heartbeat_timeout
        self.master: Any | None = None
        self._heartbeat: Any | None = None

    def _require_connection(self) -> Any:
        if self.master is None:
            raise RuntimeError("MAVLink adapter is not connected")
        return self.master

    def connect(self) -> dict[str, Any]:
        self.master = mavutil.mavlink_connection(
            self.connection_string, baud=self.baud, autoreconnect=False
        )
        heartbeat = self.master.wait_heartbeat(timeout=self.heartbeat_timeout)
        if heartbeat is None:
            self.disconnect()
            raise TimeoutError(
                f"No MAVLink heartbeat within {self.heartbeat_timeout:g}s on "
                f"{self.connection_string} at {self.baud} baud"
            )
        self._heartbeat = heartbeat
        return self.get_vehicle_identity()

    def disconnect(self) -> None:
        if self.master is not None:
            close = getattr(self.master, "close", None)
            if callable(close):
                close()
        self.master = None

    def get_heartbeat(self, timeout: float | None = None) -> dict[str, Any]:
        master = self._require_connection()
        heartbeat = master.recv_match(
            type="HEARTBEAT", blocking=True, timeout=timeout or self.heartbeat_timeout
        )
        if heartbeat is None:
            raise TimeoutError("Timed out waiting for MAVLink HEARTBEAT")
        self._heartbeat = heartbeat
        return message_dict(heartbeat)

    def get_vehicle_identity(self) -> dict[str, Any]:
        master = self._require_connection()
        heartbeat = self._heartbeat
        if heartbeat is None:
            raise RuntimeError("Vehicle identity is unavailable before a heartbeat")
        base_mode = int(heartbeat.base_mode)
        return {
            "system_id": master.target_system,
            "component_id": master.target_component,
            "vehicle_type": int(heartbeat.type),
            "vehicle_type_name": mavutil.mavlink.enums["MAV_TYPE"].get(
                int(heartbeat.type), type("Unknown", (), {"name": "UNKNOWN"})
            ).name,
            "autopilot_type": int(heartbeat.autopilot),
            "autopilot_type_name": mavutil.mavlink.enums["MAV_AUTOPILOT"].get(
                int(heartbeat.autopilot), type("Unknown", (), {"name": "UNKNOWN"})
            ).name,
            "base_mode": base_mode,
            "custom_mode": int(heartbeat.custom_mode),
            "flight_mode": mavutil.mode_string_v10(heartbeat),
            "armed": bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED),
            "system_status": int(heartbeat.system_status),
        }

    def _receive(self, message_type: str | list[str], timeout: float) -> Any | None:
        return self._require_connection().recv_match(
            type=message_type, blocking=True, timeout=timeout
        )

    def get_firmware_version(self, timeout: float = 5.0) -> dict[str, Any] | None:
        master = self._require_connection()
        # MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES is a read-only information request.
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        message = self._receive("AUTOPILOT_VERSION", timeout)
        if message is None:
            return None
        data = message_dict(message)
        flight_version = int(data.get("flight_sw_version", 0))
        custom = data.get("flight_custom_version")
        if isinstance(custom, (bytes, bytearray)):
            custom = bytes(custom).hex()
        data["firmware_version"] = firmware_version_from_uint(flight_version)
        data["flight_custom_version"] = custom
        return data

    def get_gps(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive("GPS_RAW_INT", timeout)
        return parse_gps_raw(message_dict(message)) if message else None

    def get_global_position(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive("GLOBAL_POSITION_INT", timeout)
        return parse_global_position(message_dict(message)) if message else None

    def get_attitude(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive("ATTITUDE", timeout)
        return parse_attitude(message_dict(message)) if message else None

    def get_heading(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive(["GLOBAL_POSITION_INT", "VFR_HUD"], timeout)
        if message is None:
            return None
        data = message_dict(message)
        if message.get_type() == "GLOBAL_POSITION_INT":
            return {"heading_deg": parse_global_position(data)["heading_deg"]}
        return {"heading_deg": data.get("heading")}

    def get_battery(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive("SYS_STATUS", timeout)
        return parse_sys_status(message_dict(message)) if message else None

    def get_system_status(self, timeout: float = 2.0) -> dict[str, Any] | None:
        message = self._receive(["SYS_STATUS", "EKF_STATUS_REPORT"], timeout)
        return {"message_type": message.get_type(), **message_dict(message)} if message else None

    def get_parameters_snapshot(
        self, timeout: float = 60.0, idle_timeout: float = 3.0
    ) -> dict[str, float]:
        master = self._require_connection()
        master.mav.param_request_list_send(master.target_system, master.target_component)
        parameters: dict[str, float] = {}
        started = time.monotonic()
        last_received = started
        expected: int | None = None
        while time.monotonic() - started < timeout:
            remaining_idle = idle_timeout - (time.monotonic() - last_received)
            if remaining_idle <= 0:
                break
            message = master.recv_match(
                type="PARAM_VALUE", blocking=True, timeout=min(1.0, remaining_idle)
            )
            if message is None:
                continue
            parameter_id = message.param_id
            if isinstance(parameter_id, bytes):
                parameter_id = parameter_id.decode("ascii", errors="replace")
            parameters[str(parameter_id).rstrip("\x00")] = float(message.param_value)
            expected = int(message.param_count)
            last_received = time.monotonic()
            if expected >= 0 and len(parameters) >= expected:
                break
        return dict(sorted(parameters.items()))

    def get_capabilities(self) -> dict[str, Any]:
        version = self.get_firmware_version()
        return {
            "capabilities_bitmask": version.get("capabilities") if version else None,
            "source": "AUTOPILOT_VERSION" if version else None,
        }

