# Stage 8S Robustness Report

## Hardware Gate

- H1: **PENDING**
- Physical hardware touched: **NO**
- All flight/failsafe evidence in this report is normalized injection or local ArduCopter SITL evidence. Physical behavior is unverified.

## Campaign

- Scenarios: 22
- Critical: 10/10 passed
- Recoverable/noncritical: 12/12 passed
- Normalized campaign result: **PASS**

| Scenario | Phase | Detected | Correct response | Drop prevented | Final safe state | Recovered | Result |
|---|---|---|---|---|---|---|---|
| browser_loss_auto | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| websocket_drop_auto | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| mavlink_loss_guided | ALIGNMENT | YES | OBSERVE_AUTOPILOT_FAILSAFE | YES | RETURNING | NO | PASS |
| guided_watchdog | ALIGNMENT | YES | REQUEST_RTL | YES | RETURNING | NO | PASS |
| low_battery_search | AUTO_SEARCH | YES | DEFER_TARGET | YES | SEARCHING | YES | PASS |
| critical_battery_guided | GUIDED_APPROACH | YES | OBSERVE_AUTOPILOT_FAILSAFE | YES | RETURNING | NO | PASS |
| ekf_failure_guided | ALIGNMENT | YES | OBSERVE_AUTOPILOT_FAILSAFE | YES | RETURNING | NO | PASS |
| gps_loss_guided | GUIDED_APPROACH | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| geofence_breach | GUIDED_APPROACH | YES | OBSERVE_AUTOPILOT_FAILSAFE | YES | RETURNING | NO | PASS |
| vision_crash_search | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| vision_crash_alignment | ALIGNMENT | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| camera_freeze_alignment | ALIGNMENT | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| detector_freeze_alignment | ALIGNMENT | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| stale_detection | TARGET_CONFIRMATION | YES | DEFER_TARGET | YES | SEARCHING | YES | PASS |
| target_loss_before_drop | ALIGNMENT | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| deadline_pressure_alignment | ALIGNMENT | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| payload_verification_failure | DELIVERY | YES | ABORT_TARGET_SERVICE | YES | SEARCHING | YES | PASS |
| database_outage | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| backend_restart_auto | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| orchestrator_restart_auto | AUTO_SEARCH | YES | CONTINUE | YES | SEARCHING | YES | PASS |
| orchestrator_restart_guided | GUIDED_APPROACH | YES | REQUEST_RTL | YES | RETURNING | NO | PASS |
| emergency_recall_guided | ALIGNMENT | YES | REQUEST_RTL | YES | RETURNING | NO | PASS |

## Native ArduPilot failsafe evidence

- Emergency RTL/recall: **PASS** — actual GUIDED → RTL → landed/disarmed; no payload release.
- Live artifact: C:\Users\nanda\Downloads\SURVIVOR_DETECTION\data\resilience_runs\live_stage8s_recall_rerun\test_emergency_recall_during_g0\rescue_runs\2026-08-29_15-28-59_46f8a87d_stage8s-live-recall\execution_summary.json
- ArduCopter: 4.8.0
- Commit: af2a1bafc8ab993418e6714cbc2674dcc178d11a
- Connection: tcp:127.0.0.1:5760
- Mode evidence: AUTO → GUIDED → RTL
- GCS/C2, battery, EKF and geofence: normalized-policy campaign implemented; native sensor/parameter injection is **NOT_TESTED** unless a separate live artifact is present.
- Fresh disposable SITL instances are required for any scenario parameter changes.

## Competition readiness

- Overall: **NOT_COMPETITION_READY**
- MISSION INPUT: PASS — Offline boundary validation and planning implemented
- AUTONOMY: PASS — Single-drone rescue flow and resilience policies pass in SITL
- GCS: PASS — One offline operator interface with competition lock
- NETWORK: PASS — Offline compliance scanner result
- FAILSAFES: PENDING — SITL evidence exists; physical failsafes remain unverified
- VIDEO: PENDING — Live video is simulated/not physically verified
- PAYLOAD: PENDING — Payload release and feedback are simulated only
- HARDWARE: PENDING — H1_PHYSICAL_PROTOTYPE is PENDING
- FLEET: FAIL — Two-or-more-drone fleet evidence was not included in this Stage 8S assessment
- MASS: PENDING — 25 kg combined mass not measured
- LAUNCH AREA: PENDING — 12 ft x 12 ft compliance not physically tested


## Physical gaps

- Pi 5, Pixhawk/M8N and OV9281 integration: PENDING (H1)
- Physical failsafes and payload: NOT VERIFIED
- Real aerial survivor recall and live video: NOT VERIFIED
- Two-or-more drone fleet: NOT IMPLEMENTED
- 25 kg mass and 12 ft x 12 ft launch/landing compliance: NOT MEASURED/NOT PHYSICALLY TESTED
