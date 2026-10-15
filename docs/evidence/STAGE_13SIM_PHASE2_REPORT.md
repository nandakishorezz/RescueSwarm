# STAGE 13SIM — PHASE 2 FINAL QUALIFICATION

Date: 2026-09-09  
Overall: **PASS**  
Gold run: `stage13sim_phase2e_gold_20260909a`

## Foundation

Direct Gazebo/AP source clock: VALID; P95 0.000480 ms.  
Pose sidecar join: 8278 joined, 161 missed (98.09%).  
Pose values: MAVLink EKF3; host authoritative: NO; Gazebo truth pose: NO; quaternion SLERP: PASS.

## Live gold

Rendered/inferred frames: 2686; YOLO detections: 54; Stage-5 observations: 54.  
Hidden/visible/detected/geolocated/confirmed: 3/3/3/3/3. Confirmed false-positive tracks: 1.  
Target horizontal error: best 0.875 m, worst 1.077 m; uncertainty contained 2/3.

## Controlled experiments

FOV: PASS; occlusion: PASS; wind/pixel: PASS.  
Camera pitch: PASS; altitude: PASS; speed: PASS.  
Timing control changed the same detection by 0.264 m at +100 ms and 0.535 m at +200 ms.  
Motion blur model: NOT_ACTIVE_PHASE2.

## Isolation, replay, termination, resources

Truth firewall: PASS; evaluator-disabled causality: PASS; truth-overlay causality: PASS; source-time replay: PASS.  
RTL/grounded/disarmed: True/True/True.  
Memory: PASS (600 simulated seconds; unbounded growth: FALSE).  
Offline runtime: PASS.

## Regression

Python: 361 passed, 9 skipped. Frontend: 36 passed.  
Frontend build: PASS; SITL incremental build: PASS.

## Gate matrix

| Gate | Result |
|---|---|
| direct clock | PASS |
| source-time pose | PASS |
| three-survivor gold | PASS |
| FOV | PASS |
| occlusion | PASS |
| wind/pixel | PASS |
| camera pitch | PASS |
| altitude | PASS |
| speed | PASS |
| uncertainty evaluation | PASS |
| truth firewall | PASS |
| evaluator disabled | PASS |
| truth overlay | PASS |
| replay | PASS |
| final disarm | PASS |
| bounded memory | PASS |
| offline | PASS |
| regression | PASS |

## Known limitations

- Camera calibration and mount values are engineering simulation references, not physical calibration.
- Flat ground is the Phase-2 ground model; motion blur is intentionally not active until Phase 3.
- Three targets are insufficient for a statistical uncertainty-calibration claim; one of three gold truths lay 0.077 m outside the 1.0 m reported radius.

Phase 3 was not started. Physical hardware was not touched.
