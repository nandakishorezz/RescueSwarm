# Pixel-Only Simulation Perception

Status: PARTIAL / BLOCKED, recovered 2026-09-06. Hardware gate H1 is PENDING.
No physical hardware was accessed. Phase 3 was not started.

The existing implementation is retained:

`Gazebo world -> OGRE2 camera -> Gazebo Transport -> loopback bridge ->
GazeboCameraSource -> FramePacket -> VisionAgent -> ONNX DetectorBackend ->
Stage-5 geolocation -> SpatiotemporalTargetTracker`.

The final two steps are currently gated off for Gazebo frames because the
capture-to-autopilot clock mapping is not verified. Raw detections remain intact;
rejections explicitly record `CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED`.
No receipt-time estimate is accepted as frame-time geolocation evidence.

The camera is OV9281 reference simulation, 1280x800 at configured 30 Hz,
PHYSICAL_UNVERIFIED. Gazebo currently renders L_INT8 directly; RGB transport is
also supported through deterministic integer luminance conversion. MONO is
replicated into three channels for the existing detector. Debug overlays are
created only after inference on separate images.

The local YOLO11n ONNX model is explicitly selected despite the generic
development config defaulting to mock. Model hash validation remains unchanged.
No model download, training, threshold reduction, or synthetic bbox is used.

## Recovery Commands

From the repository root on Windows with the existing Ubuntu WSL environment:

```powershell
python -B -m pytest -p no:cacheprovider -q
python -B -m scripts.probe_stage13sim_phase2_asset --frames 20 --output data/simulation/<unique_control_id>
python -B -m scripts.run_stage13sim_phase2 --run-id <unique_run_id> --wind PERCEPTION_MODERATE --altitude-m 8 --speed-mps 3 --max-sim-time-s 75
```

The live runner exits 2 while qualification is blocked. This is intentional,
not evidence that no pixels were received. Run directories cannot be reused.
The static asset probe is explicitly NON-GOLD ENGINEERING CONTROL.

## Current Evidence

`data/simulation_runs/stage13sim_phase2_recovery_labels_20260906/` contains the
latest corrected evaluator run. All three hidden humans were detected; none
were confirmed or geolocated. See `STAGE_13SIM_PHASE2_REPORT.md` for metrics.

This remains a high-fidelity software simulation development result, not
real-world validation, OV9281 accuracy, or measured airframe performance.
MOTION BLUR MODEL: NOT YET ENABLED.

## Missing Acceptance Work

Capture-time mapping, confirmation timing review, bounded long-duration event
history, complete wind/pixel series, FOV and occlusion controls, angle/altitude/
speed campaigns, frame-linked replay, observer mask display, and UI-controlled
mission/perception startup are not yet qualified. Do not advance to Phase 3.
