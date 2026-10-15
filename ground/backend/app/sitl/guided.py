"""pymavlink GUIDED commands isolated behind the Stage 7S navigator contract."""

from __future__ import annotations

import math
import time

from pymavlink import mavutil

from rescue_core.autonomy.approach import ApproachPoint
from rescue_core.autonomy.guided import GuidedNavigator
from rescue_core.autonomy.safety import require_sitl_command_adapter

from ..services.coordinates import LocalCoordinateFrame
from .adapter import SITLAutopilotAdapter


class SITLGuidedNavigator(GuidedNavigator):
    def __init__(self, adapter: SITLAutopilotAdapter, mission_frame: LocalCoordinateFrame | None = None):
        require_sitl_command_adapter(adapter)
        self.adapter = adapter
        self.mission_frame = mission_frame
        self.last_command_monotonic: float | None = None
        self.commanded_yaw_rad: float | None = None

    def enter_guided(self) -> None:
        self.adapter.set_guided()

    def set_speed(self, speed_m_s: float) -> None:
        self.adapter.set_speed(speed_m_s)

    def goto_local(self, point: ApproachPoint) -> None:
        master = self.adapter._master()
        ignore_velocity_acceleration_yaw = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        if self.commanded_yaw_rad is None:
            ignore_velocity_acceleration_yaw |= mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        yaw = self.commanded_yaw_rad or 0.0
        if self.mission_frame is not None:
            target = self.mission_frame.enu_to_wgs84(
                point.east_m, point.north_m, point.altitude_m,
            )
            master.mav.set_position_target_global_int_send(
                0, master.target_system, master.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                ignore_velocity_acceleration_yaw,
                int(round(target.latitude * 10_000_000)),
                int(round(target.longitude * 10_000_000)),
                point.altitude_m,
                0, 0, 0, 0, 0, 0, yaw, 0,
            )
        else:
            # Retained for isolated navigator tests whose points are explicitly
            # in the flight controller's own LOCAL_NED origin.
            master.mav.set_position_target_local_ned_send(
                0, master.target_system, master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                ignore_velocity_acceleration_yaw,
                point.north_m, point.east_m, -point.altitude_m,
                0, 0, 0, 0, 0, 0, yaw, 0,
            )
        self.last_command_monotonic = time.monotonic()

    def set_velocity_local(self, east_m_s: float, north_m_s: float, down_m_s: float = 0) -> None:
        master = self.adapter._master()
        velocity_only = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        if self.commanded_yaw_rad is None:
            velocity_only |= mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        yaw = self.commanded_yaw_rad or 0.0
        master.mav.set_position_target_local_ned_send(
            0, master.target_system, master.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            velocity_only,
            0, 0, 0,
            north_m_s, east_m_s, down_m_s,
            0, 0, 0, yaw, 0,
        )
        self.last_command_monotonic = time.monotonic()

    def hold(self) -> None:
        self.set_velocity_local(0, 0, 0)

    def face_local(self, target_east_m: float, target_north_m: float,
                   vehicle_east_m: float, vehicle_north_m: float) -> None:
        east = target_east_m - vehicle_east_m
        north = target_north_m - vehicle_north_m
        if math.hypot(east, north) < 0.05:
            return
        self.commanded_yaw_rad = math.atan2(east, north)
        self.adapter.command_yaw(math.degrees(self.commanded_yaw_rad))

    def set_mission_current(self, sequence: int) -> None:
        self.adapter.set_mission_current(sequence)

    def resume_auto(self) -> None:
        self.adapter.resume_auto()
