#!/usr/bin/env python3
"""Publish bounded physical-bench telemetry health without any flight controls."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.common.diagnostics import write_json
from rescue_core.hardware.live import PhysicalTelemetrySession
from rescue_core.hardware.profiles import load_local_hardware_profile
from rescue_core.hardware.safety import PhysicalBenchSafetyGuard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=ROOT / "config" / "local_hardware.yaml")
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "hardware_status.json")
    parser.add_argument("--propellers-removed", action="store_true")
    args = parser.parse_args()
    confirmed = args.propellers_removed or os.getenv("RESCUESWARM_PROPELLERS_REMOVED") == "YES"
    PhysicalBenchSafetyGuard().require_propellers_removed(confirmed)
    profile = load_local_hardware_profile(args.profile)
    if not profile.autopilot.read_only:
        raise RuntimeError("Local hardware profile is not marked read-only")
    stop = False
    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    session = PhysicalTelemetrySession(profile.autopilot.connection, profile.autopilot.baud)
    try:
        identity = session.start()
        while not stop:
            summary = session.collect(2, timesync_interval_s=1)
            write_json(args.status, {
                "updated_at": datetime.now(timezone.utc).isoformat(), "source": "PHYSICAL BENCH",
                "read_only": True, "flight_commands_sent": False, "identity": identity,
                "timesync": summary["timesync"], "pose_history": summary["pose_history"],
                "gps_stationary": summary["gps_stationary"],
            })
    finally:
        session.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
