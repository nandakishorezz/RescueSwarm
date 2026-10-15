# Pixel-to-ground survivor geolocation

`CameraModel.pixel_to_ray()` uses calibrated OpenCV pinhole-Brown or fisheye unprojection. Production geometry never uses a guessed field-of-view formula. The camera ray is transformed camera-optical → body-FRD → NED → ENU in centralized modules.

The Stage 5 baseline ground model is a mission-local flat plane because the prototype has neither rangefinder nor terrain model. Rays toward the sky, rays nearly parallel to the plane, intersections behind the camera, unreasonable ranges, missing calibration, stale pose, unsynchronized clocks, invalid GPS and excessive view angle are rejected.

Both bounding-box center and bottom-center strategies are explicit and stored with every observation. No latitude/longitude is derived until after ENU intersection, using Stage 2's existing WGS84/ENU frame.

Uncertainty combines available GNSS accuracy, configured priors when telemetry is absent, calibration reprojection, pixel, attitude, mount, height/view geometry, pose age and sync quality. Every configured assumption is named in the observation. Detection confidence is never converted into metres. A shared GNSS bias floor is preserved during fusion, so repeated views cannot manufacture precision.

For future physical evaluation, use provenance-bearing ground truth and:

```bash
python tools/evaluate_geolocation.py targets.json ground_truth.json \
  --origin-latitude LAT --origin-longitude LON --origin-altitude-m ALT \
  --output geolocation_metrics.json
```

The evaluator reports per-target east/north/radial error plus mean, median, RMSE, P50, P90, P95, and maximum. Phone GPS must not be labelled surveyed/RTK truth.

