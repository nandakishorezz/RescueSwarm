from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from pymavlink import mavutil

from ground.backend.app.sitl.telemetry import SITLTelemetryNormalizer


class Message(SimpleNamespace):
    def get_type(self) -> str:
        return self.message_type


def test_normalizes_required_arducopter_messages() -> None:
    normalizer = SITLTelemetryNormalizer()
    messages = [
        Message(
            message_type="HEARTBEAT",
            base_mode=mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
            custom_mode=3,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            type=mavutil.mavlink.MAV_TYPE_QUADROTOR,
            system_status=mavutil.mavlink.MAV_STATE_ACTIVE,
            mavlink_version=3,
        ),
        Message(
            message_type="GLOBAL_POSITION_INT",
            lat=129716001,
            lon=775946002,
            relative_alt=15250,
            vx=300,
            vy=400,
            hdg=12345,
        ),
        Message(message_type="GPS_RAW_INT", fix_type=4, satellites_visible=17),
        Message(message_type="ATTITUDE", roll=0.1, pitch=-0.2, yaw=math.pi / 2),
        Message(message_type="SYS_STATUS", voltage_battery=16100, battery_remaining=78),
        Message(message_type="MISSION_CURRENT", seq=4, total=12),
        Message(message_type="MISSION_ITEM_REACHED", seq=3),
        Message(message_type="EXTENDED_SYS_STATE", landed_state=mavutil.mavlink.MAV_LANDED_STATE_IN_AIR),
        Message(message_type="STATUSTEXT", text=b"Reached command #3\x00"),
    ]
    for message in messages:
        snapshot = normalizer.handle(message)

    vehicle = snapshot.vehicle
    assert vehicle.connected and vehicle.armed and not vehicle.simulated
    assert vehicle.latitude == pytest.approx(12.9716001)
    assert vehicle.longitude == pytest.approx(77.5946002)
    assert vehicle.relative_altitude_m == pytest.approx(15.25)
    assert vehicle.ground_speed_m_s == pytest.approx(5)
    assert vehicle.roll_deg == pytest.approx(math.degrees(0.1))
    assert vehicle.pitch_deg == pytest.approx(math.degrees(-0.2))
    assert vehicle.yaw_deg == pytest.approx(90)
    assert vehicle.battery_voltage == pytest.approx(16.1)
    assert vehicle.battery_percent == 78
    assert vehicle.gps_fix_type == 4 and vehicle.satellites == 17
    assert snapshot.current_mission_item == 4 and snapshot.total_mission_items == 12
    assert snapshot.reached_items == [3]
    assert snapshot.landed_state == mavutil.mavlink.MAV_LANDED_STATE_IN_AIR
    assert snapshot.status_text[-1] == "Reached command #3"
