"""Autopilot-specific conversion from a validated plan to MAVLink mission items."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pymavlink import mavutil

from pymavlink import mavutil

from ..planning.models import CoveragePlan
from rescue_core.terrain.provider import TerrainProvider


class MissionItemDefinition(BaseModel):
    seq: int = Field(ge=0)
    frame: int
    command: int
    current: int = 0
    autocontinue: int = 1
    param1: float = 0
    param2: float = 0
    param3: float = 0
    param4: float = 0
    x: int = 0
    y: int = 0
    z: float = 0
    mission_type: int = mavutil.mavlink.MAV_MISSION_TYPE_MISSION


class MissionDefinition(BaseModel):
    mission_id: str
    items: list[MissionItemDefinition]
    fingerprint: str

    @model_validator(mode="after")
    def validate_sequence_and_type(self) -> "MissionDefinition":
        if [item.seq for item in self.items] != list(range(len(self.items))):
            raise ValueError("Mission item sequence must be continuous from zero")
        if any(item.mission_type != mavutil.mavlink.MAV_MISSION_TYPE_MISSION for item in self.items):
            raise ValueError("Stage 3 may only build MAV_MISSION_TYPE_MISSION")
        return self


def canonical_mission_payload(mission_id: str, items: list[MissionItemDefinition]) -> dict[str, Any]:
    def normalized(item: MissionItemDefinition) -> dict[str, Any]:
        payload = item.model_dump(mode="json")
        if item.seq == 0 and item.command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
            # ArduPilot replaces the reserved home slot with current home.
            payload.update({"x": 0, "y": 0, "z": 0.0})
        if item.command in {
            mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        } and item.seq > 0:
            if item.frame in {
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            }:
                payload["frame"] = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
        elif item.command in {
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        }:
            payload["frame"] = mavutil.mavlink.MAV_FRAME_MISSION
        return payload
    return {
        "mission_id": mission_id,
        "items": [normalized(item) for item in items],
    }


def mission_fingerprint(mission_id: str, items: list[MissionItemDefinition]) -> str:
    encoded = json.dumps(
        canonical_mission_payload(mission_id, items),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ArduCopterMissionBuilder:
    """Build TAKEOFF -> speed -> safe search path -> explicit RTL."""

    def build(
        self,
        plan: CoveragePlan,
        include_speed_command: bool = True,
        include_rtl: bool = True,
        terrain: TerrainProvider | None = None,
    ) -> MissionDefinition:
        if not plan.validation.valid:
            raise ValueError("Cannot build an ArduCopter mission from an invalid coverage plan")
        if not plan.path_points_wgs84:
            raise ValueError("Coverage plan has no waypoints")
        items: list[MissionItemDefinition] = []

        def append(**values: Any) -> None:
            items.append(MissionItemDefinition(seq=len(items), **values))

        # ArduPilot reserves mission sequence zero as a non-executed home slot
        # and replaces its contents with current home. Supplying the canonical
        # placeholder preserves TAKEOFF as the first executable mission item.
        append(
            frame=mavutil.mavlink.MAV_FRAME_GLOBAL,
            command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
        )
        append(
            frame=mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            command=mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            z=round(plan.configuration.search_altitude_m * 4) / 4.0,
        )
        if include_speed_command:
            append(
                frame=mavutil.mavlink.MAV_FRAME_MISSION,
                command=mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
                param1=1,
                param2=plan.configuration.search_speed_m_s,
                param3=-1,
            )
        for point, enu_point in zip(plan.path_points_wgs84, plan.path_points_enu):
            terrain_z = terrain.get_elevation_enu(enu_point.east_m, enu_point.north_m) if terrain else 0.0
            append(
                frame=mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                x=round(point.latitude * 10_000_000),
                y=round(point.longitude * 10_000_000),
                z=round((plan.configuration.search_altitude_m + terrain_z) * 4) / 4.0,
            )
        if include_rtl:
            append(
                frame=mavutil.mavlink.MAV_FRAME_MISSION,
                command=mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
            )
        fingerprint = mission_fingerprint(plan.mission_id, items)
        return MissionDefinition(mission_id=plan.mission_id, items=items, fingerprint=fingerprint)
