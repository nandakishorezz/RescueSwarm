"""Command-capable adapter that is structurally restricted to ArduCopter SITL."""

from __future__ import annotations

import os
import time
from typing import Any

from pymavlink import mavutil

from .guard import SITLSafetyGuard
from .mission import MissionDefinition
from .pose import MAVLinkPoseSubscriber
from .protocol import MAVLinkMissionProtocol, MissionTransferResult, MissionVerificationResult
from .router import MAVLinkMessageRouter
from .vehicle_source import SITLVehicleSource


class SITLReadinessError(RuntimeError):
    pass


class SITLCommandError(RuntimeError):
    pass


class SITLAutopilotAdapter:
    """Simulation-only command layer; it does not extend the Stage 1 adapter."""

    def __init__(
        self,
        connection: str,
        environment: str,
        command_authority: str,
        heartbeat_timeout_s: float = 60,
        command_timeout_s: float = 10,
    ):
        self.endpoint = SITLSafetyGuard(environment, command_authority).validate(connection)
        self.command_authority = command_authority
        self.connection_string = self.endpoint.connection_string
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.command_timeout_s = command_timeout_s
        self.master: Any | None = None
        self.router: MAVLinkMessageRouter | None = None
        self.vehicle_source: SITLVehicleSource | None = None
        self.pose_subscriber: MAVLinkPoseSubscriber | None = None
        self.last_verification: MissionVerificationResult | None = None

    def connect(self, check_alive: Any = None) -> dict[str, Any]:
        # The safety guard has already rejected every physical/remote form.
        # MAVLink 2 exposes mission_type on the integer mission protocol. This is
        # deliberately selected before opening the isolated SITL connection.
        os.environ["MAVLINK20"] = "1"
        mavutil.set_dialect("ardupilotmega")
        deadline = time.monotonic() + self.heartbeat_timeout_s
        last_error: Exception | None = None
        
        # Verify the endpoint is actually ready and accepting bytes before calling mavlink_connection.
        # This prevents an infinite EOF loop if socat is running but ArduPilot is not.
        if self.connection_string.startswith("tcp:"):
            import socket, select
            parts = self.connection_string[4:].split(":")
            if len(parts) == 2:
                host, port = parts[0], int(parts[1])
                ready = False
                while time.monotonic() < deadline:
                    if check_alive is not None and not check_alive():
                        raise RuntimeError("SITL_PROCESS_EXITED")
                    try:
                        with socket.create_connection((host, port), timeout=0.5) as s:
                            s.setblocking(False)
                            ready_socks = select.select([s], [], [], 0.5)
                            if ready_socks[0]:
                                try:
                                    data = s.recv(1)
                                    if not data:
                                        # EOF immediately, ArduPilot not bound yet
                                        time.sleep(0.5)
                                        continue
                                except BlockingIOError:
                                    pass
                            ready = True
                            break
                    except (OSError, ConnectionError) as exc:
                        last_error = exc
                        time.sleep(0.5)
                if not ready:
                    raise TimeoutError(f"ENDPOINT_NOT_READY: Could not open local SITL endpoint: {last_error}")

        while self.master is None and time.monotonic() < deadline:
            if check_alive is not None and not check_alive():
                raise RuntimeError("SITL_PROCESS_EXITED")
            try:
                self.master = mavutil.mavlink_connection(
                    self.connection_string, autoreconnect=False, dialect="ardupilotmega"
                )
            except (ConnectionError, OSError) as exc:
                last_error = exc
                time.sleep(0.25)
        if self.master is None:
            raise TimeoutError(f"ENDPOINT_NOT_READY: Could not open local SITL endpoint: {last_error}")
        self.router = MAVLinkMessageRouter(self.master)
        self.vehicle_source = SITLVehicleSource(self.router)
        # Stage 5 shares this router callback; it never creates a second reader.
        self.pose_subscriber = MAVLinkPoseSubscriber(self.router)
        self.router.start()
        heartbeat = self.router.wait_for("HEARTBEAT", max(0.1, deadline - time.monotonic()))
        if heartbeat is None:
            self.disconnect()
            raise TimeoutError(f"HEARTBEAT_TIMEOUT: No SITL heartbeat within {self.heartbeat_timeout_s:g}s")
        self.master.target_system = heartbeat.get_srcSystem()
        self.master.target_component = heartbeat.get_srcComponent()
        if int(heartbeat.autopilot) != mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
            self.disconnect()
            raise SITLReadinessError("Endpoint is not ArduPilot")
        if int(heartbeat.type) not in {
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_TYPE_HEXAROTOR,
            mavutil.mavlink.MAV_TYPE_OCTOROTOR,
            mavutil.mavlink.MAV_TYPE_TRICOPTER,
        }:
            self.disconnect()
            raise SITLReadinessError("Endpoint is not an ArduCopter vehicle")
        return {
            "system_id": self.master.target_system,
            "component_id": self.master.target_component,
            "autopilot": int(heartbeat.autopilot),
            "vehicle_type": int(heartbeat.type),
            "flight_mode": mavutil.mode_string_v10(heartbeat),
        }

    def disconnect(self) -> None:
        if self.router:
            self.router.stop()
        if self.master:
            try:
                self.master.close()
            except Exception:
                pass
        self.router = None
        self.master = None
        self.pose_subscriber = None
        self.master = None

    def wait_ready(self, timeout_s: float) -> dict[str, Any]:
        router = self._router()
        deadline = time.monotonic() + timeout_s
        last_reason = "waiting for telemetry"
        next_home_request = 0.0
        next_home_initialization = 0.0
        while time.monotonic() < deadline:
            heartbeat = router.latest("HEARTBEAT")
            gps = router.latest("GPS_RAW_INT")
            position = router.latest("GLOBAL_POSITION_INT")
            home = router.latest("HOME_POSITION")
            system = router.latest("SYS_STATUS")
            ekf = router.latest("EKF_STATUS_REPORT")
            if heartbeat is None:
                last_reason = "heartbeat unavailable"
            elif gps is None or int(gps.fix_type) < 3:
                last_reason = "GPS 3D fix unavailable"
            elif position is None or (int(position.lat) == 0 and int(position.lon) == 0):
                last_reason = "global position/home unavailable"
            elif home is None or (int(home.latitude) == 0 and int(home.longitude) == 0):
                last_reason = "ArduPilot HOME_POSITION unavailable"
                # HOME_POSITION is commonly sent once when home is established.
                # This adapter may attach after Simulation Lab's readiness
                # adapter, so request the actual message instead of inferring
                # home from the mission origin or weakening the gate.
                if gps is not None and int(gps.fix_type) >= 3 and time.monotonic() >= next_home_request:
                    self.request_home_position()
                    next_home_request = time.monotonic() + 2.0
                # The JSON-model SITL can retain an unset home after a wiped
                # parameter boot.  With real non-zero GPS/global-position
                # telemetry present, MAV_CMD_DO_SET_HOME param1=1 is the
                # supported ArduPilot request to establish home at the current
                # vehicle position.  The subsequent HOME_POSITION and EKF
                # checks remain mandatory.
                if (
                    gps is not None and int(gps.fix_type) >= 3
                    and position is not None and int(position.lat) != 0 and int(position.lon) != 0
                    and time.monotonic() >= next_home_initialization
                ):
                    ack_result = self.initialize_home_at_current_position()
                    if ack_result not in (None, mavutil.mavlink.MAV_RESULT_ACCEPTED, mavutil.mavlink.MAV_RESULT_IN_PROGRESS):
                        last_reason = f"ArduPilot rejected HOME initialization (MAV_RESULT={ack_result})"
                    next_home_initialization = time.monotonic() + 5.0
            elif system is None:
                last_reason = "system health unavailable"
            elif not (
                int(system.onboard_control_sensors_health)
                & mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK
            ):
                status_text = router.latest("STATUSTEXT")
                detail = getattr(status_text, "text", "pre-arm checks are not healthy")
                if isinstance(detail, bytes):
                    detail = detail.decode(errors="replace")
                last_reason = str(detail).rstrip("\x00")
            elif ekf is None:
                last_reason = "EKF status unavailable"
            else:
                flags = int(ekf.flags)
                required_ekf = (
                    mavutil.mavlink.EKF_ATTITUDE
                    | mavutil.mavlink.EKF_VELOCITY_HORIZ
                    | mavutil.mavlink.EKF_VELOCITY_VERT
                    | mavutil.mavlink.EKF_POS_VERT_ABS
                )
                has_horiz_pos = bool(
                    (flags & mavutil.mavlink.EKF_POS_HORIZ_ABS)
                    or (flags & mavutil.mavlink.EKF_PRED_POS_HORIZ_ABS)
                )
                if (
                    flags & required_ekf != required_ekf
                    or not has_horiz_pos
                    or (flags & mavutil.mavlink.EKF_UNINITIALIZED)
                ):
                    last_reason = f"EKF position estimate not healthy (flags={flags})"
                    time.sleep(0.25)
                    continue
                return {
                    "gps_fix_type": int(gps.fix_type),
                    "satellites": int(gps.satellites_visible),
                    "latitude": int(position.lat) / 10_000_000,
                    "longitude": int(position.lon) / 10_000_000,
                    "home_latitude": int(home.latitude) / 10_000_000,
                    "home_longitude": int(home.longitude) / 10_000_000,
                    "system_status": int(heartbeat.system_status),
                    "prearm_check_healthy": True,
                    "ekf_flags": flags,
                }
            time.sleep(0.25)
        raise SITLReadinessError(f"SITL did not become ready: {last_reason}")

    def request_message_rates(self, rates_hz: dict[str, float]) -> None:
        master = self._master()
        for name, frequency in rates_hz.items():
            message_id = getattr(mavutil.mavlink, f"MAVLINK_MSG_ID_{name}", None)
            if message_id is None or frequency <= 0:
                continue
            master.mav.command_long_send(
                master.target_system, master.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                message_id, round(1_000_000 / frequency), 0, 0, 0, 0, 0,
            )

    def request_home_position(self) -> None:
        """Ask ArduPilot to retransmit its established home over this router."""
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 0,
            mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION, 0, 0, 0, 0, 0, 0,
        )

    def initialize_home_at_current_position(self) -> int | None:
        """Request a real ArduPilot SITL home from its current GPS estimate."""
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_HOME, 0,
            1, 0, 0, 0, 0, 0, 0,
        )
        ack = self._router().wait_for(
            "COMMAND_ACK", 0.75,
            lambda value: int(value.command) == mavutil.mavlink.MAV_CMD_DO_SET_HOME,
        )
        return int(ack.result) if ack is not None else None

    def firmware_version(self) -> dict[str, Any] | None:
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES, 0, 1, 0, 0, 0, 0, 0, 0,
        )
        message = self._router().wait_for("AUTOPILOT_VERSION", self.command_timeout_s)
        if message is None:
            return None
        value = int(message.flight_sw_version)
        return {
            "flight_sw_version": value,
            "version": f"{value >> 24}.{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}",
            "custom_version": bytes(message.flight_custom_version).hex(),
        }

    def upload_and_verify(self, mission: MissionDefinition) -> tuple[MissionTransferResult, MissionVerificationResult]:
        protocol = MAVLinkMissionProtocol(self._master(), self._router(), self.command_timeout_s)
        transfer = protocol.upload(mission)
        downloaded = protocol.download()
        verification = protocol.verify(mission, downloaded)
        self.last_verification = verification
        if not verification.valid:
            raise SITLCommandError("Downloaded mission verification failed: " + "; ".join(verification.errors))
        return transfer, verification

    def arm(self) -> None:
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        self._wait_armed(True)

    def disarm(self) -> None:
        """Disarm the already-validated local SITL vehicle.

        Callers remain responsible for proving a landed state first.  This
        adapter cannot be constructed for a physical endpoint.
        """
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 0, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        self._wait_armed(False)

    def force_disarm_grounded_simulation(self, *, grounded_evidence: bool) -> None:
        """Force-disarm only a proven-grounded, simulation-authority vehicle.

        External physics models may omit an ArduPilot landing-detector input
        even after the MAVLink position and velocity prove the body has
        settled.  The firmware's force-disarm parameter remains behind both
        explicit guards and is unavailable to every physical adapter.
        """
        if self.command_authority != "simulation_only" or not grounded_evidence:
            raise SITLCommandError("Forced disarm requires simulation_only authority and grounded evidence")
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 21196, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        self._wait_armed(False)

    def prepare_for_arm(self) -> None:
        self._set_mode("GUIDED")

    def start_auto_mission(self) -> None:
        self._set_mode("AUTO")
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_MISSION_START, 0, 0, 0, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_MISSION_START)

    def rtl(self) -> None:
        self._set_mode("RTL")

    def land(self) -> None:
        """Request ArduCopter's supported LAND mode; completion still needs ON_GROUND."""
        self._set_mode("LAND")

    def command_servo(self, servo_number: int, pwm: int) -> None:
        """Command one simulated payload actuator through ArduPilot MAVLink."""
        if servo_number < 1 or not 800 <= pwm <= 2200:
            raise ValueError("Invalid simulated servo command")
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0,
            servo_number, pwm, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_DO_SET_SERVO)

    def command_yaw(self, heading_deg: float) -> None:
        """Point the airframe/camera at an autonomy-derived heading in GUIDED."""
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_CONDITION_YAW, 0,
            heading_deg % 360.0, 0, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(mavutil.mavlink.MAV_CMD_CONDITION_YAW)

    def set_guided(self) -> None:
        """Enter GUIDED after the endpoint has already passed the SITL guard."""
        self._set_mode("GUIDED")

    def resume_auto(self) -> None:
        """Resume an uploaded mission without sending MISSION_START again."""
        self._set_mode("AUTO")

    def set_mission_current(self, sequence: int) -> int:
        if sequence < 1:
            raise ValueError("Refusing to resume a non-executable/home mission item")
        master = self._master()
        master.mav.mission_set_current_send(master.target_system, master.target_component, sequence)
        message = self._router().wait_for(
            "MISSION_CURRENT", self.command_timeout_s,
            lambda item: int(item.seq) == sequence,
        )
        if message is None:
            raise SITLCommandError(f"ArduCopter did not confirm mission current sequence {sequence}")
        return int(message.seq)

    def parameter_value(self, name: str) -> float | None:
        """Read one diagnostic parameter."""
        master = self._master()
        encoded = name.encode("ascii")
        master.mav.param_request_read_send(master.target_system, master.target_component, encoded, -1)
        message = self._router().wait_for(
            "PARAM_VALUE", self.command_timeout_s,
            lambda item: bytes(item.param_id).rstrip(b"\x00").decode(errors="replace") == name
            if isinstance(item.param_id, (bytes, bytearray)) else str(item.param_id).rstrip("\x00") == name,
        )
        return float(message.param_value) if message is not None else None

    def set_parameter(self, name: str, value: float) -> float | None:
        """Write one parameter and await acknowledgment/echo in SITL."""
        master = self._master()
        encoded = name.encode("ascii")
        master.mav.param_set_send(
            master.target_system, master.target_component,
            encoded, float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
        )
        message = self._router().wait_for(
            "PARAM_VALUE", self.command_timeout_s,
            lambda item: bytes(item.param_id).rstrip(b"\x00").decode(errors="replace") == name
            if isinstance(item.param_id, (bytes, bytearray)) else str(item.param_id).rstrip("\x00") == name,
        )
        return float(message.param_value) if message is not None else None

    def set_speed(self, speed_m_s: float) -> None:
        """Command cruise/ground speed via MAV_CMD_DO_CHANGE_SPEED."""
        master = self._master()
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0,
            1,  # speed type: 1 = groundspeed
            float(speed_m_s),
            -1,  # throttle: -1 = no change
            0, 0, 0, 0,
        )


    def _set_mode(self, mode: str) -> None:
        master = self._master()
        mapping = master.mode_mapping()
        if not mapping or mode not in mapping:
            raise SITLCommandError(f"ArduCopter mode is unavailable: {mode}")
        master.mav.set_mode_send(
            master.target_system, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mapping[mode]
        )
        message = self._router().wait_for(
            "HEARTBEAT", self.command_timeout_s, lambda item: mavutil.mode_string_v10(item) == mode
        )
        if message is None:
            raise SITLCommandError(f"ArduCopter did not enter {mode} within timeout")

    def _wait_command_ack(self, command: int) -> Any:
        message = self._router().wait_for(
            "COMMAND_ACK", self.command_timeout_s, lambda item: int(item.command) == command
        )
        if message is None:
            raise SITLCommandError(f"No COMMAND_ACK for command {command}")
        accepted = {mavutil.mavlink.MAV_RESULT_ACCEPTED, mavutil.mavlink.MAV_RESULT_IN_PROGRESS}
        if int(message.result) not in accepted:
            status = self._router().latest("STATUSTEXT")
            detail = getattr(status, "text", "")
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            suffix = f": {str(detail).rstrip(chr(0))}" if detail else ""
            raise SITLCommandError(
                f"Command {command} rejected with MAV_RESULT {int(message.result)}{suffix}"
            )
        return message

    def _wait_armed(self, expected: bool) -> None:
        message = self._router().wait_for(
            "HEARTBEAT",
            self.command_timeout_s,
            lambda item: bool(int(item.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) is expected,
        )
        if message is None:
            raise SITLCommandError(f"Armed state did not become {expected}")

    def _master(self) -> Any:
        if self.master is None:
            raise RuntimeError("SITL adapter is not connected")
        return self.master

    def _router(self) -> MAVLinkMessageRouter:
        if self.router is None:
            raise RuntimeError("SITL adapter is not connected")
        return self.router
