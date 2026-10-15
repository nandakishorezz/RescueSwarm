from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest
from pymavlink import mavutil

from ground.backend.app.sitl.mission import MissionDefinition, MissionItemDefinition, mission_fingerprint
from ground.backend.app.sitl.protocol import MAVLinkMissionProtocol, MissionProtocolError


class Message(SimpleNamespace):
    def get_type(self) -> str:
        return self.message_type


class FakeMav:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, (*args, kwargs)))

        return record


class FakeConnection:
    target_system = 1
    target_component = 1

    def __init__(self) -> None:
        self.mav = FakeMav()


class FakeRouter:
    def __init__(self, messages: list[Message | None]):
        self.messages = deque(messages)

    def wait_for(self, message_types, timeout_s, predicate=None):
        while self.messages:
            message = self.messages.popleft()
            if message is None:
                return None
            accepted = (message_types,) if isinstance(message_types, str) else message_types
            if message.get_type() in accepted and (predicate is None or predicate(message)):
                return message
        return None


def mission() -> MissionDefinition:
    items = [
        MissionItemDefinition(seq=0, frame=6, command=22, z=15),
        MissionItemDefinition(seq=1, frame=6, command=16, x=129716000, y=775946000, z=15),
        MissionItemDefinition(seq=2, frame=2, command=20),
    ]
    return MissionDefinition(mission_id="protocol", items=items, fingerprint=mission_fingerprint("protocol", items))


def request(sequence: int) -> Message:
    return Message(message_type="MISSION_REQUEST_INT", seq=sequence)


def ack(result: int = mavutil.mavlink.MAV_MISSION_ACCEPTED) -> Message:
    return Message(message_type="MISSION_ACK", type=result)


def test_upload_follows_requests_and_accepts_out_of_order_and_duplicate_requests() -> None:
    connection = FakeConnection()
    router = FakeRouter([request(1), request(0), request(1), request(2), ack()])
    result = MAVLinkMissionProtocol(connection, router).upload(mission())

    sent_sequences = [call[1][2] for call in connection.mav.calls if call[0] == "mission_item_int_send"]
    assert result.success
    assert sent_sequences == [1, 0, 1, 2]
    assert connection.mav.calls[0][0] == "mission_count_send"


def test_upload_timeout_is_bounded_and_retries_count() -> None:
    connection = FakeConnection()
    with pytest.raises(MissionProtocolError, match="bounded attempts"):
        MAVLinkMissionProtocol(connection, FakeRouter([None, None]), max_retries=2).upload(mission())
    assert [name for name, _ in connection.mav.calls].count("mission_count_send") == 2


def test_upload_rejects_negative_ack_and_bad_sequence() -> None:
    with pytest.raises(MissionProtocolError, match="rejected"):
        MAVLinkMissionProtocol(FakeConnection(), FakeRouter([ack(1)])).upload(mission())
    with pytest.raises(MissionProtocolError, match="out-of-range"):
        MAVLinkMissionProtocol(FakeConnection(), FakeRouter([request(99)])).upload(mission())


def test_download_and_verification_round_trip() -> None:
    expected = mission()
    messages = [Message(message_type="MISSION_COUNT", count=len(expected.items))]
    for item in expected.items:
        messages.append(Message(message_type="MISSION_ITEM_INT", **item.model_dump(exclude={"mission_type"}), mission_type=0))
    connection = FakeConnection()
    protocol = MAVLinkMissionProtocol(connection, FakeRouter(messages))

    downloaded = protocol.download()
    verification = protocol.verify(expected, downloaded)
    assert verification.valid
    assert verification.downloaded_fingerprint == expected.fingerprint


def test_verification_detects_coordinate_mismatch() -> None:
    expected = mission()
    downloaded = [item.model_copy(deep=True) for item in expected.items]
    downloaded[1].x += 5
    result = MAVLinkMissionProtocol(FakeConnection(), FakeRouter([])).verify(expected, downloaded)
    assert not result.valid
    assert any("x differs" in error for error in result.errors)
