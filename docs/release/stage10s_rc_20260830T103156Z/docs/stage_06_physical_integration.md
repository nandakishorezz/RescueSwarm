# Stage 6 — physical bench integration

Stage 6 joins the existing read-only autopilot diagnostics, vision pipeline, and geolocation math on the real Raspberry Pi 5. It is a stationary bench procedure. It does not prove aerial detector recall and it contains no physical-aircraft control path.

## Safety gate

1. **REMOVE ALL PROPELLERS.** Confirm this before opening any Pixhawk connection.
2. Power the Pi 5 and Pixhawk from appropriate independent supplies. Do not use a Pixhawk TELEM 5 V pin as the default Pi supply.
3. Stop precision tests if `power.json` reports current undervoltage, frequency capping, throttling, or a soft-temperature limit.
4. Never run the Stage 3 SITL adapter against a physical port. Stage 6 uses the Stage 1 read-only adapter and `PhysicalBenchSafetyGuard`.

The guard rejects arm, mode, mission, takeoff, goto, RTL, land, servo, motor-test, and payload operations independently of ArduPilot pre-arm checks.

## Bring-up order

Run each gate independently and retain its generated directory under `data/hardware_runs/`.

```bash
python scripts/run_stage6_hardware_baseline.py
python scripts/find_pixhawk_connection.py --propellers-removed
python scripts/run_stage6_hardware_baseline.py \
  --connection /dev/ttyACM0 --baud 115200 --propellers-removed
python scripts/run_stage6_physical_validation.py --camera
python scripts/run_stage6_physical_validation.py --timesync \
  --connection /dev/ttyACM0 --baud 115200 --duration 60 --propellers-removed
python scripts/run_stage6_physical_validation.py --capture-sync \
  --connection /dev/ttyACM0 --baud 115200 --duration 60 --propellers-removed
```

Discovery only listens for a bounded heartbeat on `/dev/ttyACM0` and `/dev/serial0` at 57600, 115200, and 921600 baud unless explicit candidates are supplied. It sends no MAVLink packet. A successful result produces ignored `config/local_hardware.yaml`; it never changes `config/prototype.yaml`.

## GPS and attitude evidence

Place the prototype stationary under open sky and run a sufficiently long sample:

```bash
python scripts/run_stage6_physical_validation.py --gps-stability \
  --connection /dev/ttyACM0 --baud 115200 --duration 600 --propellers-removed
```

The report calls the scatter **stationary repeatability**, not absolute accuracy. Absolute error is measured only when a provenance-rich surveyed/RTK truth coordinate exists. Preserve `GPS_RAW_INT`, message-rate, attitude-noise, EKF, and STATUSTEXT evidence. Do not recalibrate or write parameters during this stage.

## Camera, model, and calibration gates

Follow [OV9281 setup](ov9281_pi5_setup.md). Confirm CAM0 or CAM1 by inspection before applying an overlay. The benchmark never edits boot configuration. Required order:

1. camera enumeration and mode evidence;
2. 100–500 capture-only frames;
3. 60-second capture stability;
4. 10-minute then ideally 30-minute vision stability;
5. checkerboard capture with measured printed square size;
6. pinhole and fisheye fits plus held-out validation;
7. explicit fixed-mount extrinsics.

After a mode and benchmark pass, create the ignored evidence-backed camera profile:

```bash
python tools/create_local_vision_profile.py camera_modes.json benchmark_summary.json \
  --mode-index 0 --evidence-run RUN_ID
```

A YOLO11n ONNX baseline was exported during development, pinned by SHA-256 in `models/manifest.yaml`, and exercised through the Windows ONNX backend. Copy the ignored artifact explicitly to the Pi deployment. NCNN remains unprovisioned and `NCNN ON PI5 = NOT_TESTED`. Missing artifacts fail clearly and mission runtime never downloads them.

## Physical geolocation tests

- **Test A:** real calibrated image/pixel with tape- or laser-measured camera and target ENU; no M8N.
- **Test B:** Test A plus real Pixhawk attitude, TIMESYNC, and interpolated pose.
- **Test C:** complete OV9281 + Pi 5 + Pixhawk + M8N chain producing WGS84.

Each truth record must identify method, expected accuracy, and notes. Test A/B should use a marked local grid where practical. Test C absolute accuracy remains `NOT_MEASURED` without surveyed/RTK truth.

Evaluate a recorded session:

```bash
python tools/evaluate_stage6_physical_geolocation.py estimates.ndjson truth.json \
  --test A --output geolocation_test_a.json
```

Do not use an online map click as precise truth. Compare predicted uncertainty with actual error and report empirical coverage; do not call a radius P95 unless that interpretation is supported by the sample set.

## Endurance

After the separate gates pass, run the single-open camera detector alongside the single MAVLink reader:

```bash
python scripts/run_stage6_physical_validation.py --integration-endurance \
  --connection /dev/ttyACM0 --baud 115200 --duration 1800 --propellers-removed \
  --vision-config config/local_vision.yaml --backend onnx --model yolo11n_person_onnx \
  --calibration-id REAL_ID --sensor-mode MODE_FROM_EVIDENCE \
  --origin-lat LAT --origin-lon LON --origin-alt ALT
```

The test runs the one-open camera, real local detector, one MAVLink reader, TIMESYNC, PoseHistory, bounded geolocation worker, and Stage 5 geolocator simultaneously. It records detector/capture health, pose gaps, observation/rejection throughput, camera/MAVLink errors, memory, temperature, and throttling. It must not run a second camera consumer or a second MAVLink receive loop.

## Status rule

On a non-Pi host the only valid outcome is **STAGE 6 SOFTWARE READY — PHYSICAL VALIDATION REQUIRED**. Stage 6 is physically complete only after Tests A/B and, where suitable truth exists, Test C produce measured error evidence.

## Local SITL audit (not physical evidence)

On the 2026-08-29 Windows development host, WSL Ubuntu contained `sim_vehicle.py` at `/home/nandakishore7/ardupilot/Tools/autotest/sim_vehicle.py`. This resolves the earlier path-discovery discrepancy. Re-running the Stage 5 `sitl_geo` test is optional regression evidence and does not substitute for Pi/OV9281/Pixhawk/M8N validation.
