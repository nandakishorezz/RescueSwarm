"""Read-only physical telemetry session using the existing single MAVLink router."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber
from ground.backend.app.sitl.router import MAVLinkMessageRouter
from rescue_core.adapters.autopilot import ArduPilotMavlinkAdapter
from rescue_core.adapters.autopilot.telemetry import message_dict, parse_attitude, parse_gps_raw
from rescue_core.geolocation.pose import PoseHistory

from .metrics import attitude_static_statistics, gps_stationary_statistics, stream_rate_statistics


class PhysicalTelemetrySession:
    """One read loop for physical MAVLink, with subscriber-based pose/TIMESYNC.

    No message-rate, mode, mission, parameter-write, or actuator command is emitted.
    The only optional transmission is a MAVLink TIMESYNC information exchange.
    """

    def __init__(self, connection: str, baud: int, heartbeat_timeout_s: float = 10):
        self.adapter = ArduPilotMavlinkAdapter(connection, baud, heartbeat_timeout_s)
        self.router: MAVLinkMessageRouter | None = None
        self.pose: MAVLinkPoseSubscriber | None = None
        self.timestamps: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=20_000))
        self.gps_samples: deque[dict[str, Any]] = deque(maxlen=20_000)
        self.attitude_samples: deque[dict[str, Any]] = deque(maxlen=20_000)
        self.status_text: deque[dict[str, Any]] = deque(maxlen=500)
        self.raw_records: deque[dict[str, Any]] = deque(maxlen=100_000)

    def start(self) -> dict[str, Any]:
        identity = self.adapter.connect()
        master = self.adapter.master
        if master is None:
            raise RuntimeError("MAVLink connection missing after heartbeat")
        self.router = MAVLinkMessageRouter(master)
        self.pose = MAVLinkPoseSubscriber(self.router, history=PoseHistory(history_seconds=10))
        self.router.subscribe(self._observe)
        self.router.start()
        return identity

    def stop(self) -> None:
        if self.router:
            self.router.stop()
        self.adapter.disconnect()

    def _observe(self, message: Any) -> None:
        received = time.monotonic_ns()
        kind = message.get_type()
        self.timestamps[kind].append(received)
        fields = message_dict(message)
        if kind == "GPS_RAW_INT":
            self.gps_samples.append(parse_gps_raw(fields))
        elif kind == "ATTITUDE":
            self.attitude_samples.append(parse_attitude(fields))
        elif kind == "STATUSTEXT":
            self.status_text.append(fields)
        if kind in {"HEARTBEAT", "ATTITUDE", "ATTITUDE_QUATERNION", "GLOBAL_POSITION_INT", "LOCAL_POSITION_NED", "GPS_RAW_INT", "TIMESYNC", "EKF_STATUS_REPORT", "SYS_STATUS", "STATUSTEXT"}:
            self.raw_records.append({"received_monotonic_ns": received, "message_type": kind, "fields": fields})

    def collect(self, duration_s: float, timesync_interval_s: float | None = 1.0) -> dict[str, Any]:
        if not self.router or not self.pose:
            raise RuntimeError("PhysicalTelemetrySession is not started")
        deadline = time.monotonic() + duration_s
        next_sync = time.monotonic()
        while time.monotonic() < deadline:
            now = time.monotonic()
            if timesync_interval_s is not None and now >= next_sync:
                self.pose.request_timesync()
                next_sync = now + timesync_interval_s
            time.sleep(min(0.05, max(0, deadline - now)))
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {
            "safety": {"read_only": True, "flight_commands_sent": False, "message_rate_commands_sent": False},
            "message_rates": {kind: stream_rate_statistics(values) for kind, values in sorted(self.timestamps.items())},
            "gps_stationary": gps_stationary_statistics(self.gps_samples),
            "attitude_static": attitude_static_statistics(self.attitude_samples),
            "timesync": self.pose.clock_sync.diagnostics() if self.pose else {"sync_quality": "NOT_TESTED"},
            "pose_history": {
                "sample_count": len(self.pose.history.samples) if self.pose else 0,
                "accepted": self.pose.history.stats.accepted if self.pose else 0,
                "maximum_observed_gap_ms": self.pose.history.stats.maximum_observed_gap_ms if self.pose else None,
            },
            "status_text": list(self.status_text),
        }

    def write(self, directory: str | Path) -> None:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "telemetry.ndjson").write_text(
            "".join(json.dumps(record, default=str, sort_keys=True) + "\n" for record in self.raw_records),
            encoding="utf-8",
        )
        (output / "pose_samples.ndjson").write_text(
            "".join(sample.model_dump_json() + "\n" for sample in (self.pose.history.samples if self.pose else [])),
            encoding="utf-8",
        )
