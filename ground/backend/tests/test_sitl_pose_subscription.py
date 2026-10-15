from __future__ import annotations

from types import SimpleNamespace

import pytest

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber


class Message(SimpleNamespace):
    def __init__(self, message_type: str, **values):
        super().__init__(**values)
        self.message_type = message_type

    def get_type(self) -> str:
        return self.message_type


class FakeMav:
    def __init__(self):
        self.timesync: list[tuple[int, int]] = []

    def timesync_send(self, tc1: int, ts1: int) -> None:
        self.timesync.append((tc1, ts1))


class FakeRouter:
    def __init__(self):
        self.callbacks = []
        self.connection = SimpleNamespace(mav=FakeMav())
        self.recv_calls = 0

    def subscribe(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, message) -> None:
        for callback in self.callbacks:
            callback(message)

    def recv_match(self, *args, **kwargs):
        self.recv_calls += 1
        raise AssertionError("Stage 5 must not create a second MAVLink reader")


def test_pose_subscriber_uses_router_callback_and_prefers_quaternion(monkeypatch) -> None:
    monotonic = iter([1_000_000_000, 1_010_000_000, 1_020_000_000, 1_030_000_000])
    monkeypatch.setattr("ground.backend.app.sitl.pose.time.monotonic_ns", lambda: next(monotonic))
    router = FakeRouter()
    subscriber = MAVLinkPoseSubscriber(
        router, coordinate_frame=LocalCoordinateFrame(GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500))
    )
    router.emit(Message("ATTITUDE_QUATERNION", time_boot_ms=1000, q1=1, q2=0, q3=0, q4=0))
    router.emit(Message("GPS_RAW_INT", fix_type=3, satellites_visible=12, h_acc=2500, v_acc=4000, eph=65535, epv=65535))
    router.emit(Message(
        "GLOBAL_POSITION_INT", time_boot_ms=1020, lat=174500000, lon=783800000,
        alt=520000, relative_alt=20000, vx=400, vy=0, vz=0,
    ))
    assert len(subscriber.history.samples) == 1
    sample = subscriber.history.samples[0]
    assert sample.quaternion_body_to_ned == pytest.approx((1, 0, 0, 0))
    assert sample.horizontal_accuracy_m == pytest.approx(2.5)
    assert "ATTITUDE_QUATERNION" in sample.source_message_ids
    assert router.recv_calls == 0
    sent = subscriber.request_timesync()
    assert router.connection.mav.timesync == [(0, sent)]


def test_pose_subscriber_uses_local_ned_and_attitude_fallback(monkeypatch) -> None:
    monkeypatch.setattr("ground.backend.app.sitl.pose.time.monotonic_ns", lambda: 2_000_000_000)
    router = FakeRouter()
    subscriber = MAVLinkPoseSubscriber(router)
    router.emit(Message("ATTITUDE", time_boot_ms=2000, roll=0, pitch=0, yaw=0))
    router.emit(Message("GPS_RAW_INT", fix_type=3, satellites_visible=8, h_acc=0, v_acc=0, eph=300, epv=500))
    router.emit(Message(
        "GLOBAL_POSITION_INT", time_boot_ms=2000, lat=174500000, lon=783800000,
        alt=520000, relative_alt=20000, vx=400, vy=0, vz=0,
    ))
    assert not subscriber.history.samples  # No competing WGS84 conversion without an explicit origin.
    router.emit(Message("LOCAL_POSITION_NED", time_boot_ms=2010, x=1, y=2, z=-20, vx=4, vy=0, vz=0))
    sample = subscriber.history.samples[0]
    assert sample.local_position_ned_m == (1, 2, -20)
    assert sample.horizontal_accuracy_m == pytest.approx(3)
    assert "ATTITUDE_FALLBACK" in sample.source_message_ids


def test_explicit_mission_origin_prevents_ekf_local_origin_mixup(monkeypatch) -> None:
    monotonic = iter([3_000_000_000, 3_010_000_000, 3_020_000_000])
    monkeypatch.setattr("ground.backend.app.sitl.pose.time.monotonic_ns", lambda: next(monotonic))
    router = FakeRouter()
    origin = GeoPoint(latitude=17.45, longitude=78.38, altitude_m=500)
    subscriber = MAVLinkPoseSubscriber(router, coordinate_frame=LocalCoordinateFrame(origin))
    router.emit(Message("ATTITUDE_QUATERNION", time_boot_ms=3000, q1=1, q2=0, q3=0, q4=0))
    router.emit(Message(
        "GLOBAL_POSITION_INT", time_boot_ms=3010, lat=174500000, lon=783800000,
        alt=520000, relative_alt=20000, vx=0, vy=0, vz=0,
    ))
    router.emit(Message(
        "LOCAL_POSITION_NED", time_boot_ms=3020,
        x=99, y=-77, z=-20, vx=1, vy=2, vz=3,
    ))
    sample = subscriber.history.samples[-1]
    assert sample.local_position_ned_m == pytest.approx((0, 0, -20), abs=.01)
    assert sample.velocity_ned_m_s == (1, 2, 3)
    assert "GLOBAL_POSITION_INT_MISSION_FRAME" in sample.source_message_ids
