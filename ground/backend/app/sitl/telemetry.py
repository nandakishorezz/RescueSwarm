"""Normalize real ArduCopter SITL MAVLink into source-independent GCS state."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from pymavlink import mavutil

from ..domain.vehicle import VehicleState


class SITLTelemetrySnapshot(BaseModel):
    vehicle: VehicleState
    source: str = "SITL"
    current_mission_item: int = 0
    total_mission_items: int = 0
    reached_items: list[int] = Field(default_factory=list)
    landed_state: int | None = None
    system_status: int | None = None
    autopilot_type: int | None = None
    vehicle_type: int | None = None
    status_text: list[str] = Field(default_factory=list)
    last_heartbeat: datetime | None = None
    last_global_position: datetime | None = None


class SITLTelemetryNormalizer:
    def __init__(self, vehicle_id: str = "drone_01"):
        self.sequence = 0
        self.snapshot = SITLTelemetrySnapshot(
            vehicle=VehicleState(
                vehicle_id=vehicle_id,
                name="ArduCopter SITL 01",
                latitude=0,
                longitude=0,
                connected=False,
                flight_mode="UNKNOWN",
                simulated=False,
            )
        )

    def handle(self, message: Any) -> SITLTelemetrySnapshot:
        message_type = message.get_type()
        vehicle = self.snapshot.vehicle
        now = datetime.now(UTC)
        changed = False
        if message_type == "HEARTBEAT":
            vehicle.connected = True
            vehicle.flight_mode = mavutil.mode_string_v10(message)
            vehicle.armed = bool(int(message.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            self.snapshot.system_status = int(message.system_status)
            self.snapshot.autopilot_type = int(message.autopilot)
            self.snapshot.vehicle_type = int(message.type)
            self.snapshot.last_heartbeat = now
            changed = True
        elif message_type == "GLOBAL_POSITION_INT":
            vehicle.latitude = int(message.lat) / 10_000_000
            vehicle.longitude = int(message.lon) / 10_000_000
            vehicle.relative_altitude_m = int(message.relative_alt) / 1_000
            absolute_altitude_mm = getattr(message, "alt", None)
            if absolute_altitude_mm is not None:
                vehicle.absolute_altitude_m = int(absolute_altitude_mm) / 1_000
            velocity_north_cms = int(getattr(message, "vx", 0))
            velocity_east_cms = int(getattr(message, "vy", 0))
            velocity_down_cms = getattr(message, "vz", None)
            vehicle.ground_speed_m_s = math.hypot(velocity_north_cms, velocity_east_cms) / 100
            if velocity_down_cms is not None:
                vehicle.velocity_ned_m_s = (
                    velocity_north_cms / 100,
                    velocity_east_cms / 100,
                    int(velocity_down_cms) / 100,
                )
                vehicle.vertical_speed_m_s = -int(velocity_down_cms) / 100
            heading = int(getattr(message, "hdg", 65535))
            if heading != 65535:
                vehicle.yaw_deg = heading / 100
            self.snapshot.last_global_position = now
            changed = True
        elif message_type == "GPS_RAW_INT":
            vehicle.gps_fix_type = int(message.fix_type)
            vehicle.satellites = int(message.satellites_visible)
            changed = True
        elif message_type == "ATTITUDE":
            vehicle.roll_deg = math.degrees(float(message.roll))
            vehicle.pitch_deg = math.degrees(float(message.pitch))
            vehicle.yaw_deg = math.degrees(float(message.yaw)) % 360
            if vehicle.quaternion_body_to_ned is None:
                vehicle.attitude_source = "ATTITUDE_EULER"
            changed = True
        elif message_type == "ATTITUDE_QUATERNION":
            quaternion = (float(message.q1), float(message.q2), float(message.q3), float(message.q4))
            norm = math.sqrt(sum(value * value for value in quaternion))
            if norm > 1e-12:
                vehicle.quaternion_body_to_ned = tuple(value / norm for value in quaternion)
                vehicle.attitude_source = "ATTITUDE_QUATERNION"
            changed = True
        elif message_type == "SYS_STATUS":
            voltage = int(message.voltage_battery)
            remaining = int(message.battery_remaining)
            if voltage != 65535:
                vehicle.battery_voltage = max(0, voltage / 1_000)
            if remaining >= 0:
                vehicle.battery_percent = min(100, remaining)
            changed = True
        elif message_type == "MISSION_CURRENT":
            self.snapshot.current_mission_item = int(message.seq)
            self.snapshot.total_mission_items = int(getattr(message, "total", self.snapshot.total_mission_items))
            changed = True
        elif message_type == "MISSION_ITEM_REACHED":
            sequence = int(message.seq)
            if sequence not in self.snapshot.reached_items:
                self.snapshot.reached_items.append(sequence)
            changed = True
        elif message_type == "EXTENDED_SYS_STATE":
            self.snapshot.landed_state = int(message.landed_state)
            changed = True
        elif message_type == "STATUSTEXT":
            text = message.text.decode(errors="replace") if isinstance(message.text, bytes) else str(message.text)
            self.snapshot.status_text = [*self.snapshot.status_text[-49:], text.rstrip("\x00")]
            changed = True
        if changed:
            self.sequence += 1
            vehicle.sequence = self.sequence
            vehicle.last_update = now
        return self.snapshot.model_copy(deep=True)
