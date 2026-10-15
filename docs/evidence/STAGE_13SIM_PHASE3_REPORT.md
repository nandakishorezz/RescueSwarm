# Stage 13SIM Phase 3 Report

Date: 2026-09-09. Status: **PASS**. Hardware Gate H1 remains **PENDING**.

Sensor imperfections are injected by ArduPilot SITL sensor backends before EKF3 and by the bounded MONO8 camera measurement/transport adapter. No EKF output, bbox, target coordinate, or fused result is directly corrupted. Parameters are reference/engineering presets and physically unverified.

## Live gold

- Run: `stage13sim_phase3_nominal_gold_20260909a` (`REFERENCE_NOMINAL`, moderate physical wind).
- Direct clock: VALID; P95 0.000480 ms.
- Pose-sidecar join: 99.09%.
- Three survivors: 3 visible, 3 detected, 3 confirmed/geolocated.
- Best/worst target error: 0.628/1.396 m.
- Camera transport: 109.17 ms P50; capture timestamps preserved; no receipt/latest fallback.
- Bounded memory: PASS; offline runtime: PASS.

## Boundaries and limitations

The observer alone compares truth, raw simulated measurements, and EKF estimates. The camera is global-shutter-class; rolling shutter is not applicable. Motion blur remains unimplemented because the pinned renderer exposes no defensible exposure/motion integration hook. LLVMPipe wall performance is development evidence, not Pi 5 performance. The small target sample is not statistical uncertainty calibration.

## Final regression

- Python: 364 passed, 9 physical/live opt-in skips, 0 failed.
- Frontend: 37 passed, 0 failed; production build PASS.
- ArduPilot SITL incremental build: PASS.
- Offline runtime: PASS.
