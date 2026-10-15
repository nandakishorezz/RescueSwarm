#!/usr/bin/env python3
"""Passively find a Pixhawk heartbeat on bounded, explicitly approved bench ports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymavlink import mavutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.hardware.profiles import LocalAutopilotProfile, LocalHardwareProfile, write_local_hardware_profile
from rescue_core.hardware.safety import PhysicalBenchSafetyGuard
from rescue_core.common.diagnostics import timestamp_slug, write_json

DEFAULT_PORTS = ("/dev/ttyACM0", "/dev/serial0")
DEFAULT_BAUDS = (57600, 115200, 921600)


def discover(ports: list[str], bauds: list[int], timeout_s: float) -> tuple[dict, int]:
    attempts: list[dict] = []
    for port in ports:
        for baud in bauds:
            attempt = {"connection": port, "baud": baud, "status": "FAILED"}
            connection = None
            try:
                connection = mavutil.mavlink_connection(port, baud=baud, autoreconnect=False)
                heartbeat = connection.wait_heartbeat(timeout=timeout_s)
                if heartbeat is not None:
                    attempt.update({
                        "status": "VERIFIED", "system_id": connection.target_system,
                        "component_id": connection.target_component,
                        "autopilot_type": int(heartbeat.autopilot), "vehicle_type": int(heartbeat.type),
                        "flight_commands_sent": False, "protocol_transmissions_sent": False,
                    })
                    attempts.append(attempt)
                    return {"status": "VERIFIED", "selected": attempt, "attempts": attempts}, 0
                attempt["reason"] = "bounded heartbeat timeout"
            except Exception as exc:
                attempt["reason"] = f"{type(exc).__name__}: {exc}"
            finally:
                if connection is not None:
                    connection.close()
            attempts.append(attempt)
    return {"status": "FAILED", "reason": "No passive heartbeat on approved candidates", "attempts": attempts}, 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", action="append", dest="ports", help="Approved device; repeat to test multiple")
    parser.add_argument("--baud", action="append", type=int, choices=DEFAULT_BAUDS, dest="bauds")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--propellers-removed", action="store_true", help="Required physical bench confirmation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", type=Path, default=ROOT / "config" / "local_hardware.yaml")
    parser.add_argument("--no-write-profile", action="store_true")
    args = parser.parse_args()
    PhysicalBenchSafetyGuard().require_propellers_removed(args.propellers_removed)
    if not sys.platform.startswith("linux"):
        parser.error("Physical Pixhawk discovery runs only on Linux/Raspberry Pi")
    result, code = discover(args.ports or list(DEFAULT_PORTS), args.bauds or list(DEFAULT_BAUDS), args.timeout)
    destination = args.output or ROOT / "data" / "hardware_runs" / f"{timestamp_slug()}_connection_discovery.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, result)
    if code == 0 and not args.no_write_profile:
        selected = result["selected"]
        write_local_hardware_profile(args.profile, LocalHardwareProfile(
            autopilot=LocalAutopilotProfile(
                connection=selected["connection"], baud=selected["baud"], evidence_run=str(destination),
            ),
            notes=["Generated from a passive heartbeat; physical port/wiring must still be documented"],
        ))
        print(args.profile)
    print(destination)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
