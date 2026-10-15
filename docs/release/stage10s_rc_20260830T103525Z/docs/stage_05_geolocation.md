# Stage 5 — synchronized survivor geolocation

Stage 5 is a read-only localization layer. It consumes Stage 4 original-pixel detections and pose/timing messages dispatched by Stage 3's single MAVLink reader. It cannot arm, change mode, upload a mission, guide a vehicle, return, land, or actuate hardware.

```text
original detection pixel → calibrated camera ray → fixed camera/body transform
→ quaternion vehicle pose at frame time → ENU ray → flat-ground intersection
→ uncertainty-bearing observation → world-space association → robust fused target
```

Coordinate conventions are centralized in `rescue_core/geolocation/transforms.py`: camera optical is right/down/forward, ArduPilot body is forward/right/down, world telemetry is NED, and mission geometry is ENU. No core geolocation calculation uses latitude/longitude degrees as metres. Final WGS84 values use the existing Stage 2 `LocalCoordinateFrame`.

Every valid observation contains calibration/extrinsics IDs, frame time, bracketing pose times, interpolation fraction, clock quality, original bounding box, selected pixel, ray, ENU result, WGS84 result, uncertainty components, and evidence references. Invalid observations carry a rejection reason instead of a fabricated coordinate.

The checked-in configuration values are development defaults. In particular, standard-GNSS priors are configured assumptions—not fixed M8N specifications. Detector confidence and geographic uncertainty remain separate.

Run the Windows-safe mathematical demonstration:

```bash
python scripts/run_stage5_demo.py --monte-carlo-samples 200
```

It creates a known synthetic target, forward-projects it into pixels, runs the real inverse chain, confirms/fuses multiple observations, and measures timing, attitude, mount, common GNSS-bias, and ground-height sensitivities. It does not use a physical camera or claim physical accuracy.

Live official ArduCopter SITL validation is opt-in after ArduPilot is installed:

```bash
RESCUESWARM_RUN_SITL_GEO=1 ARDUPILOT_HOME="$HOME/ardupilot" \
  python -m pytest -m sitl_geo -s
```

The gold test uses actual SITL pose telemetry, a known virtual ground target, a synthetic calibrated camera, and the real inverse/fusion chain while the existing Stage 3 AUTO mission continues. Stage 5 only subscribes to the existing router.

