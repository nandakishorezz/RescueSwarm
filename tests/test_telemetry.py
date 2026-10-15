import math

import pytest

from rescue_core.adapters.autopilot.telemetry import (
    TelemetryAccumulator,
    firmware_version_from_uint,
    parse_attitude,
    parse_global_position,
    parse_gps_raw,
    parse_sys_status,
)


class FakeMessage:
    def __init__(self, message_type: str, **fields: object) -> None:
        self.message_type = message_type
        self.fields = fields

    def get_type(self) -> str:
        return self.message_type

    def to_dict(self) -> dict[str, object]:
        return {"mavpackettype": self.message_type, **self.fields}


def test_mavlink_scaling_and_sentinels() -> None:
    gps = parse_gps_raw(
        {"fix_type": 3, "satellites_visible": 12, "eph": 85, "epv": 65535,
         "lat": 123456789, "lon": -456789012, "alt": 12345}
    )
    assert gps["eph"] == pytest.approx(0.85)
    assert gps["hdop"] == pytest.approx(0.85)
    assert gps["epv"] is None
    assert gps["latitude_deg"] == pytest.approx(12.3456789)
    assert gps["altitude_msl_m"] == pytest.approx(12.345)

    position = parse_global_position(
        {"lat": 10000000, "lon": 20000000, "alt": 3000, "relative_alt": 1500, "hdg": 65535}
    )
    assert position["relative_altitude_m"] == 1.5
    assert position["heading_deg"] is None


def test_attitude_battery_and_firmware_conversion() -> None:
    attitude = parse_attitude({"roll": math.pi / 2, "pitch": 0.0, "yaw": -math.pi})
    assert attitude["roll_deg"] == pytest.approx(90)
    assert attitude["yaw_deg"] == pytest.approx(-180)
    battery = parse_sys_status(
        {"voltage_battery": 12100, "current_battery": 350, "battery_remaining": 76}
    )
    assert battery["voltage_v"] == pytest.approx(12.1)
    assert battery["current_a"] == pytest.approx(3.5)
    encoded = (4 << 24) | (5 << 16) | (7 << 8) | 255
    assert firmware_version_from_uint(encoded) == "4.5.7 (type 255)"


def test_accumulator_summarizes_mock_messages() -> None:
    accumulator = TelemetryAccumulator()
    accumulator.add(FakeMessage("HEARTBEAT", base_mode=0), 1.0)
    accumulator.add(FakeMessage("HEARTBEAT", base_mode=0), 2.0)
    accumulator.add(
        FakeMessage("GPS_RAW_INT", fix_type=3, satellites_visible=9, eph=120, epv=150,
                    lat=10000000, lon=20000000, alt=1000),
        2.1,
    )
    summary = accumulator.summary()
    assert summary["heartbeat_rate_hz"] == pytest.approx(1.0)
    assert summary["gps"]["fix_type"] == 3
    assert summary["message_counts"]["HEARTBEAT"] == 2
