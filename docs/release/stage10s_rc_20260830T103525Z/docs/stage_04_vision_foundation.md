# Stage 4 — Raspberry Pi 5 vision foundation

Stage 4 adds a bench-only, flight-independent visual pipeline. It does not import the autopilot adapter and cannot arm, change mode, upload missions, geolocate detections, track people, or command movement.

```text
Picamera2 / replay / images / synthetic
                ↓
         timestamped FramePacket
                ↓
       bounded latest-frame queue
                ↓
  grayscale-aware preprocessing/tiling
                ↓
       local DetectorBackend
                ↓
 original-image-pixel DetectionResult
                ↓
 benchmark, evidence, health and events
```

The Windows development run validates the software using `SyntheticCameraSource` and `MockDetectorBackend`. Those measurements are test-harness measurements, not Raspberry Pi, OV9281, NCNN, generic detector, or accuracy results. `STAGE_4_REPORT.md` keeps physical camera and aerial-human accuracy as `NOT_TESTED`/`NOT_MEASURED` until evidence exists.

The physical gate is ordered:

1. `CAMERA_ENUMERATION_PASS`
2. `CAPTURE_SHORT_PASS` (100 frames)
3. `CAPTURE_STABILITY_PASS` (60 seconds, then endurance)
4. `VISION_REAL_CAMERA_PASS` (local provisioned model)

Run Stage 1 camera diagnostics before changing configuration. The high-level Pi benchmark never edits boot configuration. If enumeration fails, it stops and refers the operator to the separate dry-run helper.

The engineering defaults in `config/vision.yaml` are unverified placeholders. Mode discovery on the real sensor supersedes them. Stage 4 does not infer the OV9281 field of view, final search altitude, final lane spacing, or usable aircraft speed.

See [OV9281 setup](ov9281_pi5_setup.md), [vision architecture](vision_architecture.md), [benchmarking](vision_benchmarking.md), and [model management](model_management.md).
