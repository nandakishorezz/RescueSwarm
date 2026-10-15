"""Request-driven MAVLink integer mission upload, download, and verification."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pymavlink import mavutil

from .mission import MissionDefinition, MissionItemDefinition, mission_fingerprint


class MissionProtocolError(RuntimeError):
    pass


@dataclass
class MissionTransferResult:
    success: bool
    attempts: int
    item_count: int
    acknowledgements: list[int] = field(default_factory=list)


@dataclass
class MissionVerificationResult:
    valid: bool
    expected_fingerprint: str
    downloaded_fingerprint: str
    errors: list[str] = field(default_factory=list)
    downloaded_items: list[dict[str, Any]] = field(default_factory=list)


class MAVLinkMissionProtocol:
    def __init__(self, connection: Any, router: Any, timeout_s: float = 5, max_retries: int = 3):
        self.connection = connection
        self.router = router
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    @property
    def mission_type(self) -> int:
        return mavutil.mavlink.MAV_MISSION_TYPE_MISSION

    def upload(self, mission: MissionDefinition) -> MissionTransferResult:
        for attempt in range(1, self.max_retries + 1):
            self.connection.mav.mission_count_send(
                self.connection.target_system,
                self.connection.target_component,
                len(mission.items),
                mission_type=self.mission_type,
            )
            sent: set[int] = set()
            while True:
                message = self.router.wait_for(
                    ("MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"), self.timeout_s
                )
                if message is None:
                    break
                if message.get_type() == "MISSION_ACK":
                    result = int(message.type)
                    if result != mavutil.mavlink.MAV_MISSION_ACCEPTED:
                        raise MissionProtocolError(f"ArduPilot rejected mission upload with ACK {result}")
                    if len(sent) != len(mission.items):
                        raise MissionProtocolError("Mission ACK arrived before every mission item was requested")
                    return MissionTransferResult(True, attempt, len(sent), [result])
                sequence = int(message.seq)
                if sequence < 0 or sequence >= len(mission.items):
                    raise MissionProtocolError(f"ArduPilot requested out-of-range mission sequence {sequence}")
                self._send_item(mission.items[sequence])
                sent.add(sequence)
        raise MissionProtocolError(f"Mission upload timed out after {self.max_retries} bounded attempts")

    def download(self) -> list[MissionItemDefinition]:
        self.connection.mav.mission_request_list_send(
            self.connection.target_system, self.connection.target_component,
            mission_type=self.mission_type,
        )
        count_message = self.router.wait_for("MISSION_COUNT", self.timeout_s)
        if count_message is None:
            raise MissionProtocolError("Timed out waiting for downloaded MISSION_COUNT")
        count = int(count_message.count)
        items: list[MissionItemDefinition] = []
        for sequence in range(count):
            received = None
            for _ in range(self.max_retries):
                self.connection.mav.mission_request_int_send(
                    self.connection.target_system,
                    self.connection.target_component,
                    sequence,
                    mission_type=self.mission_type,
                )
                received = self.router.wait_for(
                    ("MISSION_ITEM_INT", "MISSION_ITEM"),
                    self.timeout_s,
                    lambda message, expected=sequence: int(message.seq) == expected,
                )
                if received is not None:
                    break
            if received is None:
                raise MissionProtocolError(f"Timed out downloading mission item {sequence}")
            items.append(self._definition_from_message(received))
        self.connection.mav.mission_ack_send(
            self.connection.target_system,
            self.connection.target_component,
            mavutil.mavlink.MAV_MISSION_ACCEPTED,
            mission_type=self.mission_type,
        )
        return items

    def verify(self, expected: MissionDefinition, downloaded: list[MissionItemDefinition]) -> MissionVerificationResult:
        errors: list[str] = []
        if len(expected.items) != len(downloaded):
            errors.append(f"Item count differs: expected {len(expected.items)}, downloaded {len(downloaded)}")
        for index, (wanted, actual) in enumerate(zip(expected.items, downloaded)):
            integer_fields = ("seq", "command", "mission_type") if index == 0 else (
                "seq", "command", "mission_type", "x", "y"
            )
            for field_name in integer_fields:
                if getattr(wanted, field_name) != getattr(actual, field_name):
                    errors.append(
                        f"Item {index} {field_name} differs "
                        f"(expected {getattr(wanted, field_name)}, got {getattr(actual, field_name)})"
                    )
            frame_matches = wanted.frame == actual.frame
            if wanted.command in {
                mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            } and wanted.seq > 0:
                frame_matches = {wanted.frame, actual.frame} <= {
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                }
            elif wanted.command in {
                mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
                mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
            }:
                frame_matches = actual.frame in {mavutil.mavlink.MAV_FRAME_GLOBAL, mavutil.mavlink.MAV_FRAME_MISSION}
            if not frame_matches:
                errors.append(f"Item {index} frame differs (expected {wanted.frame}, got {actual.frame})")
            float_fields = ("param1", "param2", "param3", "param4") if index == 0 else (
                "param1", "param2", "param3", "param4", "z"
            )
            for field_name in float_fields:
                if not math.isclose(getattr(wanted, field_name), getattr(actual, field_name), abs_tol=1e-3):
                    errors.append(
                        f"Item {index} {field_name} differs "
                        f"(expected {getattr(wanted, field_name)}, got {getattr(actual, field_name)})"
                    )
        downloaded_fingerprint = mission_fingerprint(expected.mission_id, downloaded)
        if downloaded_fingerprint != expected.fingerprint:
            errors.append("Canonical mission fingerprint differs")
        return MissionVerificationResult(
            not errors,
            expected.fingerprint,
            downloaded_fingerprint,
            errors,
            [item.model_dump(mode="json") for item in downloaded],
        )

    def _send_item(self, item: MissionItemDefinition) -> None:
        self.connection.mav.mission_item_int_send(
            self.connection.target_system,
            self.connection.target_component,
            item.seq,
            item.frame,
            item.command,
            item.current,
            item.autocontinue,
            item.param1,
            item.param2,
            item.param3,
            item.param4,
            item.x,
            item.y,
            item.z,
            mission_type=item.mission_type,
        )

    @staticmethod
    def _definition_from_message(message: Any) -> MissionItemDefinition:
        is_int = message.get_type() == "MISSION_ITEM_INT"
        x = int(message.x) if is_int else round(float(message.x) * 10_000_000)
        y = int(message.y) if is_int else round(float(message.y) * 10_000_000)
        return MissionItemDefinition(
            seq=int(message.seq),
            frame=int(message.frame),
            command=int(message.command),
            current=int(message.current),
            autocontinue=int(message.autocontinue),
            param1=float(message.param1),
            param2=float(message.param2),
            param3=float(message.param3),
            param4=float(message.param4),
            x=x,
            y=y,
            z=float(message.z),
            mission_type=int(getattr(message, "mission_type", mavutil.mavlink.MAV_MISSION_TYPE_MISSION)),
        )
