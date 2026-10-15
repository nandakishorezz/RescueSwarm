#!/usr/bin/env python3
"""Probe one explicitly selected MAVLink endpoint and save read-only evidence."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from rescue_core.adapters.autopilot import ArduPilotMavlinkAdapter
from rescue_core.adapters.autopilot.telemetry import TelemetryAccumulator, message_dict
from rescue_core.common.config import load_config
from rescue_core.common.diagnostics import ensure_output_directory, write_json

LOGGER = logging.getLogger("mavlink_probe")


def _source(message: Any) -> dict[str, int | None]:
    return {
        "system_id": message.get_srcSystem() if hasattr(message, "get_srcSystem") else None,
        "component_id": message.get_srcComponent() if hasattr(message, "get_srcComponent") else None,
    }


def _raw_line(message: Any, received_monotonic: float) -> str:
    record = {
        "received_monotonic": received_monotonic,
        "message_type": message.get_type(),
        **_source(message),
        "fields": message_dict(message),
    }
    return json.dumps(record, default=str, sort_keys=True)


def _write_parameters(path: Path, parameters: dict[str, float]) -> None:
    lines = ["# RescueSwarm read-only ArduPilot parameter snapshot"]
    lines.extend(f"{name},{value:.10g}" for name, value in sorted(parameters.items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _highlight_parameters(parameters: dict[str, float]) -> dict[str, float]:
    prefixes = (
        "SERIAL", "BRD", "GPS", "COMPASS", "EK3", "AHRS", "ARMING",
        "FS_", "BATT", "RTL", "FENCE", "RC", "SERVO",
    )
    return {key: value for key, value in parameters.items() if key.startswith(prefixes)}


def probe(
    connection: str,
    baud: int,
    heartbeat_timeout: float,
    collection_seconds: float,
    output_dir: Path,
    collect_parameters: bool = True,
    parameter_timeout: float = 60.0,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {
        "status": "NOT_TESTED",
        "connection": connection,
        "baud": baud,
        "safety": {
            "read_only": True,
            "requests_sent": ["AUTOPILOT_VERSION", "PARAM_REQUEST_LIST"]
            if collect_parameters
            else ["AUTOPILOT_VERSION"],
            "flight_commands_sent": False,
        },
    }
    adapter = ArduPilotMavlinkAdapter(connection, baud, heartbeat_timeout)
    raw_path = output_dir / "mavlink_raw.jsonl"
    try:
        identity = adapter.connect()
        result.update({"status": "VERIFIED", "heartbeat": "VERIFIED", "identity": identity})
        accumulator = TelemetryAccumulator()
        deadline = time.monotonic() + collection_seconds
        with raw_path.open("w", encoding="utf-8") as raw_stream:
            while time.monotonic() < deadline:
                message = adapter.master.recv_match(blocking=True, timeout=0.5)
                if message is None:
                    continue
                now = time.monotonic()
                accumulator.add(message, now)
                if message.get_type() != "BAD_DATA":
                    raw_stream.write(_raw_line(message, now) + "\n")
        result["telemetry"] = accumulator.summary()
        try:
            result["firmware"] = adapter.get_firmware_version(timeout=5.0)
        except Exception as exc:  # Preserve a successful heartbeat when one read request fails.
            result["firmware"] = None
            result["firmware_error"] = f"{type(exc).__name__}: {exc}"
        if collect_parameters:
            try:
                parameters = adapter.get_parameters_snapshot(timeout=parameter_timeout)
                result["parameters"] = {
                    "status": "VERIFIED" if parameters else "FAILED",
                    "count": len(parameters),
                    "file": "parameters.param" if parameters else None,
                    "highlighted": _highlight_parameters(parameters),
                }
                if parameters:
                    _write_parameters(output_dir / "parameters.param", parameters)
            except Exception as exc:
                result["parameters"] = {
                    "status": "FAILED",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
        else:
            result["parameters"] = {
                "status": "NOT_TESTED",
                "reason": "Parameter collection skipped by operator",
            }
        return result, 0
    except Exception as exc:
        result.update(
            {
                "status": "FAILED",
                "heartbeat": "FAILED",
                "reason": f"{type(exc).__name__}: {exc}",
                "parameters": {
                    "status": "NOT_TESTED",
                    "reason": "No live MAVLink connection",
                },
            }
        )
        return result, 2
    finally:
        adapter.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/prototype.yaml")
    parser.add_argument("--connection", help="Serial path or udp:/tcp: pymavlink endpoint")
    parser.add_argument("--baud", type=int, choices=(57600, 115200, 921600))
    parser.add_argument("--heartbeat-timeout", type=float)
    parser.add_argument("--duration", type=float, help="Passive collection seconds (10-30 recommended)")
    parser.add_argument("--parameter-timeout", type=float, default=60.0)
    parser.add_argument("--skip-parameters", action="store_true")
    parser.add_argument("--output-dir", help="Existing diagnostic run directory")
    args = parser.parse_args()
    config = load_config(args.config).autopilot
    connection = args.connection or config.connection
    baud = args.baud or config.baud
    timeout = args.heartbeat_timeout or config.heartbeat_timeout_seconds
    duration = args.duration or config.collection_seconds
    if not 10 <= duration <= 30:
        parser.error("--duration must be between 10 and 30 seconds")
    output = ensure_output_directory(args.output_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOGGER.info("Opening only the configured endpoint %s at %s baud", connection, baud)
    result, exit_code = probe(
        connection,
        baud,
        timeout,
        duration,
        output,
        collect_parameters=not args.skip_parameters,
        parameter_timeout=args.parameter_timeout,
    )
    write_json(output / "mavlink.json", result)
    print(output / "mavlink.json")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
