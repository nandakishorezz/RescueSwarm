#!/usr/bin/env python3
"""Generate STAGE_1_REPORT.md from collected evidence without inventing results."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _status(value: bool | None) -> str:
    return "VERIFIED" if value is True else "FAILED" if value is False else "NOT_TESTED"


def report_rows(run_dir: Path | None) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    system = _load(run_dir / "system.json") if run_dir else None
    serial = _load(run_dir / "serial.json") if run_dir else None
    mavlink = _load(run_dir / "mavlink.json") if run_dir else None
    camera = _load(run_dir / "camera.json") if run_dir else None
    evidence = {"system": system, "serial": serial, "mavlink": mavlink, "camera": camera}

    is_pi = system.get("platform", {}).get("raspberry_pi") if system else None
    serial_state: bool | None = None
    serial_detail = "No serial diagnostic result"
    if serial:
        configured = serial.get("configured_connection_status")
        serial_state = True if configured == "exists" else False if configured == "missing" else None
        serial_detail = str(configured or "No configured connection supplied")

    heartbeat = mavlink.get("heartbeat") if mavlink else None
    identity = mavlink.get("identity", {}) if mavlink else {}
    autopilot_name = str(identity.get("autopilot_type_name", "UNKNOWN"))
    autopilot_status = (
        "VERIFIED"
        if "ARDUPILOT" in autopilot_name.upper()
        else "FAILED" if identity else "NOT_TESTED"
    )
    telemetry = mavlink.get("telemetry", {}) if mavlink else {}
    firmware = mavlink.get("firmware") if mavlink else None
    gps = telemetry.get("gps") if telemetry else None
    attitude = telemetry.get("attitude") if telemetry else None
    battery = telemetry.get("battery") if telemetry else None
    heading = telemetry.get("heading_deg") if telemetry else None
    params = mavlink.get("parameters", {}) if mavlink else {}
    cam_state = camera.get("status") if camera else None
    camera_verified = cam_state == "DETECTED"

    rows = [
        ("Raspberry Pi identified", _status(is_pi), system.get("platform", {}).get("model", "") if system else "No system result"),
        ("Serial device exists", _status(serial_state), serial_detail),
        ("MAVLink heartbeat", heartbeat or "NOT_TESTED", mavlink.get("reason", "") if mavlink else "No MAVLink result"),
        ("Autopilot identified", autopilot_status, autopilot_name),
        ("Firmware version", "VERIFIED" if firmware else "UNKNOWN" if mavlink else "NOT_TESTED", firmware.get("firmware_version", "UNKNOWN") if firmware else "UNKNOWN"),
        ("GPS data received", "VERIFIED" if gps else "FAILED" if heartbeat == "VERIFIED" else "NOT_TESTED", "GPS_RAW_INT" if gps else "No GPS_RAW_INT evidence"),
        ("GPS fix", "VERIFIED" if gps and gps.get("fix_type", 0) >= 3 else "PARTIAL" if gps else "NOT_TESTED", str(gps.get("fix_type", "UNKNOWN")) if gps else "UNKNOWN"),
        ("Satellite count", "VERIFIED" if gps and gps.get("satellites_visible") is not None else "UNKNOWN" if gps else "NOT_TESTED", str(gps.get("satellites_visible", "UNKNOWN")) if gps else "UNKNOWN"),
        ("Compass/heading", "VERIFIED" if heading is not None else "UNKNOWN" if heartbeat == "VERIFIED" else "NOT_TESTED", str(heading if heading is not None else "UNKNOWN")),
        ("Battery telemetry", "VERIFIED" if battery else "UNKNOWN" if heartbeat == "VERIFIED" else "NOT_TESTED", str(battery or "No SYS_STATUS evidence")),
        ("Attitude telemetry", "VERIFIED" if attitude else "FAILED" if heartbeat == "VERIFIED" else "NOT_TESTED", str(attitude or "No ATTITUDE evidence")),
        ("Camera enumerated", "VERIFIED" if camera_verified else "FAILED" if camera else "NOT_TESTED", camera.get("reason", "No camera result") if camera else "No camera result"),
        ("OV9281 detected", "VERIFIED" if camera_verified else "FAILED" if camera else "NOT_TESTED", cam_state or "No camera result"),
        ("Parameters backed up", params.get("status", "NOT_TESTED"), str(params.get("count", params.get("reason", "No parameter result")))),
    ]
    return rows, evidence


def generate_report(run_dir: Path | None) -> str:
    rows, evidence = report_rows(run_dir)
    source = str(run_dir) if run_dir else "None (hardware diagnostics have not been run)"
    lines = [
        "# RescueSwarm Stage 1 Report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Evidence directory: `{source}`",
        "",
        "> A configured component is not considered physically verified. This report only promotes states when supporting diagnostic evidence is present.",
        "",
        "| Component / Test | Status | Evidence / Value |",
        "|---|---|---|",
    ]
    for component, status, detail in rows:
        safe_detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {component} | {status} | {safe_detail} |")
    missing = [name for name, value in evidence.items() if value is None]
    lines.extend(["", "## Missing evidence", ""])
    if missing:
        lines.extend(f"- `{name}.json`: NOT_TESTED (file absent)" for name in missing)
    else:
        lines.append("- None of the four primary evidence files are missing.")
    lines.extend([
        "",
        "## Safety record",
        "",
        "Stage 1 diagnostics do not arm, change mode, move the vehicle, upload a mission, alter parameters, flash firmware, calibrate hardware, or actuate a payload.",
        "",
    ])
    return "\n".join(lines)


def _latest_run(base: Path) -> Path | None:
    candidates = sorted((path for path in base.iterdir() if path.is_dir()), reverse=True) if base.is_dir() else []
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="Diagnostic timestamp directory")
    parser.add_argument("--latest", action="store_true", help="Use latest data/diagnostics run")
    parser.add_argument("--output", type=Path, default=Path("STAGE_1_REPORT.md"))
    args = parser.parse_args()
    run_dir = args.run_dir
    if args.latest:
        run_dir = _latest_run(Path("data/diagnostics"))
    args.output.write_text(generate_report(run_dir), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
