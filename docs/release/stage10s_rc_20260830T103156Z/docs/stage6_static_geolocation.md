# Static physical geolocation protocol

This protocol measures the chain instead of merely showing a coordinate.

## Fixture record

Choose the body origin explicitly (normally the autopilot/EKF reference) and measure the fixed camera translation from that point. Record roll, pitch, yaw, their estimated uncertainty, the flat-ground definition, target pose, target type, lighting, calibration ID, sensor mode, and pixel-anchor strategy.

For local truth, a JSON record is:

```json
{
  "target_id": "bench_target_01",
  "source": "TAPE_MEASURED_LOCAL",
  "expected_accuracy_m": 0.02,
  "east_m": 3.0,
  "north_m": 2.0,
  "notes": "Steel tape on marked level grid; origin is camera ground projection"
}
```

Use a static volunteer only with no propellers or motors. Record standing, sitting, and prone scenes separately. Engineering tags may establish fixture geometry but are not evidence that the person detector works.

## Required evidence per estimate

Preserve frame ID/image, detection and confidence, original-pixel anchor, intrinsics/extrinsics IDs, frame timestamp, TIMESYNC quality, bracketing poses, GPS state, estimate, uncertainty, truth provenance, and resulting east/north/radial error.

Measure BBOX_CENTER and BBOX_BOTTOM_CENTER separately. A standing-person result does not settle the strategy for a prone person. Repeated static frames also provide pixel scatter, confidence distribution, and miss rate for measured detector priors.

## Interpretation

- Test A isolates intrinsics, distortion, extrinsics, anchor, and ground geometry.
- Test B adds Pixhawk attitude and timing.
- Test C adds M8N position/altitude and exposes global bias.

Tight repeatability plus a stable offset is not high absolute accuracy. Multi-view averaging does not remove a common GNSS bias. Bench person detection is not aerial-human recall.
