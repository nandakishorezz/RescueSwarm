# STAGE 6 — Physical Hardware Integration Report

> Safety: **REMOVE PROPELLERS.** This stage is read-only and sends no arm, mode, mission, motor, servo, takeoff, landing, RTL, or payload command.

Overall status: **PHYSICAL VALIDATION INCOMPLETE**

## HARDWARE

| Component | Evidence status | Detail |
|---|---|---|
| Raspberry Pi 5 | NOT_TESTED | Not identified as Raspberry Pi |
| Pixhawk link | NOT_TESTED | Physical board identity requires label/photo evidence |
| M8N | NOT_TESTED | Missing telemetry fields remain UNKNOWN |
| OV9281 | NOT_TESTED | Historical UC-580 Rev.B identity is not physical proof |

## POWER

- Undervoltage now: UNKNOWN
- Historical undervoltage: UNKNOWN
- Thermal throttling now: UNKNOWN
- Precision measurement valid: False

## MAVLINK

- Connection: UNKNOWN
- Baud: UNKNOWN
- Heartbeat: UNKNOWN
- ArduPilot version: UNKNOWN
- Observed message counts: `{}`

## GPS

- Fix: UNKNOWN
- Satellites: UNKNOWN
- Stationary repeatability: NOT_TESTED
- Absolute accuracy: NOT_MEASURED

## CAMERA / VISION / CALIBRATION

- Enumeration: NOT_TESTED
- Mode and achieved FPS: NOT_TESTED unless present in local camera evidence
- 60-second stability: NOT_TESTED
- 30-minute stability: NOT_TESTED
- Detector performance: NOT_TESTED
- Physical calibration ID: NOT_TESTED
- Camera extrinsics: NOT_TESTED

### Development model provisioning

- YOLO11n ONNX artifact: SOFTWARE TESTED ON WINDOWS
- SHA-256: 417c59a7ff8ddd316d9863fb8ba0e7f3cfc5a27264c774a4ad3ec7526f2a5e2d
- NCNN on Pi 5: NOT_TESTED
- Real-camera person detection: NOT_TESTED
- Aerial human recall: NOT_MEASURED

## TIMESYNC / FRAME-POSE SYNC

- TIMESYNC: NOT_TESTED
- Frame/pose bracketing: NOT_TESTED

## NON-PHYSICAL SITL REGRESSION

- Stage 5 geolocation gold test: PASS
- ArduPilot commit: af2a1bafc8ab993418e6714cbc2674dcc178d11a
- Physical evidence: **NO**

## PHYSICAL GEOLOCATION

- Test A, camera geometry: NOT_TESTED
- Test B, Pixhawk attitude/timing: NOT_TESTED
- Test C, complete M8N chain: NOT_TESTED
- Physical accuracy validated: **NO**

## INTEGRATION ENDURANCE

- Duration: NOT_TESTED
- Camera/MAVLink failures: NOT_TESTED
- RAM growth / maximum temperature / throttling: NOT_TESTED

## VERDICT

- Software preparation: READY
- Physical hardware validation: REQUIRED
- Aerial person detection: NOT VALIDATED
- Physical flight: NOT PERFORMED
