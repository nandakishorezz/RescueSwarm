# Spatial coordinate visualization

The frontend mirrors the Stage 5 convention and never becomes the authoritative geolocator.

```text
CAMERA OPTICAL: x right, y down, z forward
BODY FRD:       x forward, y right, z down
NED:            north, east, down
ENU:            east, north, up
THREE DISPLAY:  x east, y up, z south (-north)
```

All swaps/sign changes live in `ground/frontend/src/spatial/frames.ts`. Camera unprojection and ground intersection live in `ground/frontend/src/spatial/camera.ts`. React components consume these functions rather than embedding coordinate fixes.

Quaternion `body_to_world` is authoritative. Euler angles are display/fallback data only. Replay orientation uses normalized quaternion SLERP; interpolation is refused across stale gaps. Raw telemetry and rendered interpolation remain separate.

The shared fixture `data/fixtures/stage11s_spatial_geometry.json` is evaluated by Python and TypeScript tests. It verifies principal-point unprojection, body/camera composition, a nadir ray, ENU ground intersection, and range. Any frontend/backend disagreement beyond numerical tolerance is a hard test failure. Backend target coordinates always win; Engineering mode reports reconstruction discrepancy rather than replacing them.

For a selected observation the Spatial Inspector displays mission ENU, vehicle NED/ENU/WGS84, quaternion/Euler data, camera intrinsics/extrinsics, selected pixel, camera/body/world rays, ground intersection, range, incidence angle, and the matrices `T_world_body`, `T_body_camera`, and `T_world_camera`.
