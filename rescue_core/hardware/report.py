"""Stage 6 evidence report rendering."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "NOT_TESTED"}


def _value(value: Any) -> str:
    return "UNKNOWN" if value is None else str(value)


def render_stage6_report(run_directory: str | Path | None) -> str:
    run = Path(run_directory) if run_directory else None
    system = _load(run / "system.json") if run else {"status": "NOT_TESTED"}
    power = _load(run / "power.json") if run else {"status": "NOT_TESTED"}
    mavlink = _load(run / "mavlink_probe.json") if run else {"status": "NOT_TESTED"}
    gps = _load(run / "gps.json") if run else {"status": "NOT_TESTED"}
    camera = _load(run / "camera_probe.json") if run else {"status": "NOT_TESTED"}
    geo = _load(run / "physical_geolocation.json") if run else {"status": "NOT_TESTED"}
    sitl_audit = _load(run / "sitl_geo_regression.json") if run else {"status": "NOT_TESTED"}
    artifact = Path(__file__).resolve().parents[2] / "models" / "artifacts" / "yolo11n.onnx"
    model_hash = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.is_file() else None
    pi_model = str(system.get("platform", {}).get("model", ""))
    pi = bool(system.get("platform", {}).get("raspberry_pi", False) and "Raspberry Pi 5" in pi_model)
    firmware = (mavlink.get("firmware") or {}).get("firmware_version")
    telemetry = mavlink.get("telemetry") or {}
    return f"""# STAGE 6 — Physical Hardware Integration Report

> Safety: **REMOVE PROPELLERS.** This stage is read-only and sends no arm, mode, mission, motor, servo, takeoff, landing, RTL, or payload command.

Overall status: **{'PHYSICAL VALIDATION INCOMPLETE' if not pi else 'EVIDENCE COLLECTED — REVIEW REQUIRED'}**

## HARDWARE

| Component | Evidence status | Detail |
|---|---|---|
| Raspberry Pi 5 | {'VERIFIED' if pi else 'NOT_TESTED'} | {_value(system.get('platform', {}).get('model'))} |
| Pixhawk link | {_value(mavlink.get('status'))} | Physical board identity requires label/photo evidence |
| M8N | {_value(gps.get('status', 'NOT_TESTED'))} | Missing telemetry fields remain UNKNOWN |
| OV9281 | {_value(camera.get('status'))} | Historical UC-580 Rev.B identity is not physical proof |

## POWER

- Undervoltage now: {_value(power.get('undervoltage_now'))}
- Historical undervoltage: {_value(power.get('undervoltage_occurred'))}
- Thermal throttling now: {_value(power.get('throttled_now'))}
- Precision measurement valid: {_value(power.get('valid_for_precision_measurement'))}

## MAVLINK

- Connection: {_value(mavlink.get('connection'))}
- Baud: {_value(mavlink.get('baud'))}
- Heartbeat: {_value(mavlink.get('heartbeat'))}
- ArduPilot version: {_value(firmware)}
- Observed message counts: `{telemetry.get('message_counts', {})}`

## GPS

- Fix: {_value(gps.get('fix_type'))}
- Satellites: {_value(gps.get('satellites_visible'))}
- Stationary repeatability: {_value(gps.get('stationary_repeatability', 'NOT_TESTED'))}
- Absolute accuracy: {_value(gps.get('absolute_accuracy', 'NOT_MEASURED'))}

## CAMERA / VISION / CALIBRATION

- Enumeration: {_value(camera.get('status'))}
- Mode and achieved FPS: NOT_TESTED unless present in local camera evidence
- 60-second stability: NOT_TESTED
- 30-minute stability: NOT_TESTED
- Detector performance: NOT_TESTED
- Physical calibration ID: NOT_TESTED
- Camera extrinsics: NOT_TESTED

### Development model provisioning

- YOLO11n ONNX artifact: {'SOFTWARE TESTED ON WINDOWS' if model_hash else 'NOT_PROVISIONED'}
- SHA-256: {_value(model_hash)}
- NCNN on Pi 5: NOT_TESTED
- Real-camera person detection: NOT_TESTED
- Aerial human recall: NOT_MEASURED

## TIMESYNC / FRAME-POSE SYNC

- TIMESYNC: NOT_TESTED
- Frame/pose bracketing: NOT_TESTED

## NON-PHYSICAL SITL REGRESSION

- Stage 5 geolocation gold test: {_value(sitl_audit.get('status'))}
- ArduPilot commit: {_value(sitl_audit.get('ardupilot_commit'))}
- Physical evidence: **NO**

## PHYSICAL GEOLOCATION

- Test A, camera geometry: {_value(geo.get('test_a', 'NOT_TESTED'))}
- Test B, Pixhawk attitude/timing: {_value(geo.get('test_b', 'NOT_TESTED'))}
- Test C, complete M8N chain: {_value(geo.get('test_c', 'NOT_TESTED'))}
- Physical accuracy validated: **NO**

## INTEGRATION ENDURANCE

- Duration: NOT_TESTED
- Camera/MAVLink failures: NOT_TESTED
- RAM growth / maximum temperature / throttling: NOT_TESTED

## VERDICT

- Software preparation: READY
- Physical hardware validation: {'REQUIRED' if not pi else 'REVIEW MEASURED EVIDENCE'}
- Aerial person detection: NOT VALIDATED
- Physical flight: NOT PERFORMED
"""
