"""Stage 5 pose/TIMESYNC subscriber attached to the Stage 3 single MAVLink reader."""

from __future__ import annotations

import math
import time
from typing import Any

from rescue_core.geolocation.clock_sync import ClockSyncEstimator
from rescue_core.geolocation.pose import PoseHistory, PoseSample
from rescue_core.geolocation.transforms import enu_to_ned, quaternion_from_euler
from rescue_core.simulation.perception.clock import SimulationPoseTimestampWitness

from ..domain.mission import GeoPoint
from ..services.coordinates import LocalCoordinateFrame


class MAVLinkPoseSubscriber:
    """Consumes router callbacks only; it never opens or reads a MAVLink connection."""

    def __init__(
        self, router: Any, vehicle_id: str = "drone_01",
        history: PoseHistory | None = None, clock_sync: ClockSyncEstimator | None = None,
        coordinate_frame: LocalCoordinateFrame | None = None,
        sample_callback: Any | None = None,
        source_time_witness: SimulationPoseTimestampWitness | None = None,
        message_snoop: Any | None = None,
        require_sim_source: bool = False,
    ):
        self.router = router
        self.vehicle_id = vehicle_id
        self.history = history or PoseHistory()
        self.clock_sync = clock_sync or ClockSyncEstimator()
        self.coordinate_frame = coordinate_frame
        self.sample_callback = sample_callback
        self.source_time_witness = source_time_witness
        self.message_snoop = message_snoop
        self.require_sim_source = require_sim_source
        self.fallback_samples = 0
        self.fallback_after_first_source = 0
        self._first_source_seen = False
        self.carry_hits = 0  # retained metric; authoritative source never carries
        self.fallback_join_attempts = 0
        self._source_rows: dict[int, Any] = {}
        self._source_row_order: list[int] = []
        self.join_debug: list[dict[str, object]] = []
        self.latest: dict[str, Any] = {}
        self.emitted_samples = 0
        self.router.subscribe(self.handle)

    def set_origin(self, origin: GeoPoint) -> None:
        self.coordinate_frame = LocalCoordinateFrame(origin)

    def request_timesync(self) -> int:
        """Send an information-only TIMESYNC probe on the existing routed connection."""
        sent_ns = time.monotonic_ns()
        self.router.connection.mav.timesync_send(0, sent_ns)
        return sent_ns

    def handle(self, message: Any) -> None:
        message_type = message.get_type()
        received_monotonic_ns = time.monotonic_ns()
        self.latest[message_type] = message
        if message_type == "TIMESYNC":
            tc1, ts1 = int(message.tc1), int(message.ts1)
            if tc1 > 0 and ts1 > 0:
                self.clock_sync.observe_response(ts1, tc1, received_monotonic_ns)
            return
        if (self.source_time_witness is not None
                and message_type in {"GLOBAL_POSITION_INT", "ATTITUDE_QUATERNION", "ATTITUDE"}):
            self._join_message_source(message, received_monotonic_ns)
        # For SITL source-time mode a coherent pose snapshot is emitted on
        # GLOBAL_POSITION_INT. Normal MAVLink/physical behavior remains the
        # existing GLOBAL/LOCAL-triggered assembly path.
        if self.source_time_witness is not None:
            if message_type != "GLOBAL_POSITION_INT":
                return
        elif message_type not in {"GLOBAL_POSITION_INT", "LOCAL_POSITION_NED"}:
            return
        sample = self._assemble(received_monotonic_ns)
        if sample is None:
            return
        if sample.timestamp_source != "SITL_SIM_SOURCE_SIDECAR":
            self.fallback_samples += 1
            if self._first_source_seen:
                self.fallback_after_first_source += 1
            if self.require_sim_source:
                return
        else:
            self._first_source_seen = True
        self.history.add(sample)
        self.emitted_samples += 1
        if self.sample_callback is not None:
            self.sample_callback(sample)

    def _join_message_source(self, message: Any, host_monotonic_ns: int) -> None:
        """Join one decoded wire message exactly once by its MAVLink header."""
        msgid = int(message.get_msgId())
        sysid = int(message.get_srcSystem())
        compid = int(message.get_srcComponent())
        seq = int(message.get_seq())
        if self.message_snoop is not None:
            self.message_snoop(host_monotonic_ns, msgid,
                               int(getattr(message, "time_boot_ms", 0)), seq)
        record = self.source_time_witness.join(
            0, sysid, compid, msgid, seq,
            int(getattr(message, "time_boot_ms", 0)))
        if record is None:
            self.fallback_join_attempts += 1
            if len(self.join_debug) < 80:
                self.join_debug.append({
                    "kind": "EXACT_IDENTITY_JOIN_MISS", "chan": 0,
                    "msgid": msgid, "sysid": sysid, "compid": compid,
                    "mavlink_sequence": seq,
                    "sidecar_family_pending": self.source_time_witness.family_rows(
                        0, sysid, compid, msgid),
                })
            return
        object_key = id(message)
        self._source_rows[object_key] = record
        self._source_row_order.append(object_key)
        if len(self._source_row_order) > 2048:
            old = self._source_row_order.pop(0)
            self._source_rows.pop(old, None)

    def _assemble(self, host_monotonic_ns: int) -> PoseSample | None:
        attitude_q = self.latest.get("ATTITUDE_QUATERNION")
        attitude = self.latest.get("ATTITUDE")
        global_position = self.latest.get("GLOBAL_POSITION_INT")
        local_position = self.latest.get("LOCAL_POSITION_NED")
        if global_position is None or (attitude_q is None and attitude is None):
            return None
        time_boot_ms = int(getattr(global_position, "time_boot_ms", 0))
        if local_position is not None:
            time_boot_ms = max(time_boot_ms, int(getattr(local_position, "time_boot_ms", 0)))
        if attitude_q is not None:
            time_boot_ms = max(time_boot_ms, int(getattr(attitude_q, "time_boot_ms", 0)))
            quaternion = (
                float(attitude_q.q1), float(attitude_q.q2),
                float(attitude_q.q3), float(attitude_q.q4),
            )
            roll = pitch = yaw = None
            attitude_source = "ATTITUDE_QUATERNION"
        else:
            time_boot_ms = max(time_boot_ms, int(getattr(attitude, "time_boot_ms", 0)))
            roll, pitch, yaw = float(attitude.roll), float(attitude.pitch), float(attitude.yaw)
            quaternion = tuple(float(value) for value in quaternion_from_euler(roll, pitch, yaw))
            attitude_source = "ATTITUDE_FALLBACK"
        latitude = int(global_position.lat) / 10_000_000
        longitude = int(global_position.lon) / 10_000_000
        if self.coordinate_frame:
            # Normalize position to the configured mission origin. ArduPilot's
            # EKF LOCAL_POSITION_NED origin is not guaranteed to equal an
            # imported mission boundary origin (especially in multi-SITL), so
            # mixing those coordinate systems can invalidate boundary checks.
            enu = self.coordinate_frame.to_enu(GeoPoint(
                latitude=latitude, longitude=longitude, altitude_m=int(global_position.alt) / 1000
            ))
            local_ned = tuple(float(value) for value in enu_to_ned([enu.east_m, enu.north_m, enu.up_m]))
            velocity = (
                (float(local_position.vx), float(local_position.vy), float(local_position.vz))
                if local_position is not None
                else (int(global_position.vx) / 100, int(global_position.vy) / 100, int(global_position.vz) / 100)
            )
            position_source = "GLOBAL_POSITION_INT_MISSION_FRAME"
        elif local_position is not None:
            local_ned = (float(local_position.x), float(local_position.y), float(local_position.z))
            velocity = (float(local_position.vx), float(local_position.vy), float(local_position.vz))
            position_source = "LOCAL_POSITION_NED"
        else:
            return None
        gps = self.latest.get("GPS_RAW_INT")
        horizontal_accuracy, vertical_accuracy = self._gps_accuracy(gps)

        sim_source_ns, timestamp_source = self._sim_source_time(
            global_position, attitude_q if attitude_q is not None else attitude)
        return PoseSample(
            vehicle_id=self.vehicle_id, autopilot_boot_time_ns=sim_source_ns or time_boot_ms * 1_000_000,
            host_monotonic_time_ns=host_monotonic_ns, host_wall_time_ns=time.time_ns(),
            latitude=latitude, longitude=longitude,
            absolute_altitude_m=int(global_position.alt) / 1000,
            relative_altitude_m=int(global_position.relative_alt) / 1000,
            local_position_ned_m=local_ned, quaternion_body_to_ned=quaternion,
            roll_rad=roll, pitch_rad=pitch, yaw_rad=yaw, velocity_ned_m_s=velocity,
            gps_fix=int(gps.fix_type) if gps is not None else None,
            satellites=int(gps.satellites_visible) if gps is not None else None,
            horizontal_accuracy_m=horizontal_accuracy, vertical_accuracy_m=vertical_accuracy,
            accuracy_sources={
                "horizontal": "GPS_RAW_INT_TELEMETRY" if horizontal_accuracy is not None else "UNKNOWN",
                "vertical": "GPS_RAW_INT_TELEMETRY" if vertical_accuracy is not None else "UNKNOWN",
            },
            source_message_ids=[position_source, attitude_source, *( ["GPS_RAW_INT"] if gps else [])],
            sim_source_time_ns=sim_source_ns,
            timestamp_source=timestamp_source,
        )

    def _sim_source_time(self, global_position: Any,
                         attitude_message: Any) -> tuple[int | None, str | None]:
        """Return a coherent estimator snapshot time from independently joined messages."""
        if self.source_time_witness is None:
            return None, None
        position_row = self._source_rows.get(id(global_position))
        attitude_row = self._source_rows.get(id(attitude_message))
        if position_row is None or attitude_row is None:
            return None, "MAVLINK_BOOT_FALLBACK"
        # These messages are independently timestamped. GCS normally emits
        # them in one scheduler update; using the later timestamp defines the
        # coherent pose snapshot without pretending their boot fields match.
        return max(int(position_row.ap_sim_ns), int(attitude_row.ap_sim_ns)), \
            "SITL_SIM_SOURCE_SIDECAR"

    @staticmethod
    def _gps_accuracy(gps: Any | None) -> tuple[float | None, float | None]:
        if gps is None:
            return None, None
        h_acc, v_acc = getattr(gps, "h_acc", None), getattr(gps, "v_acc", None)
        if h_acc not in (None, 0, 0xFFFFFFFF) and v_acc not in (None, 0, 0xFFFFFFFF):
            return float(h_acc) / 1000, float(v_acc) / 1000
        eph, epv = int(getattr(gps, "eph", 65535)), int(getattr(gps, "epv", 65535))
        return (None if eph == 65535 else eph / 100, None if epv == 65535 else epv / 100)
