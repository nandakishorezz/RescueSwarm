# 3D digital twin

MapLibre owns geographic camera and map state. A locally bundled Three.js custom layer uses the same MapLibre projection for in-map aircraft rendering; the standalone 3D and split view use the same normalized domain snapshot and central spatial transforms.

The renderer includes procedural multirotor geometry, actual ENU position and altitude, quaternion attitude, search/diversion/RTL trajectories, sectors, targets and uncertainty disks, launch zone, body/world axes, camera optical axis, calibrated corner rays, valid ground footprint, and selected detection ray. Rotor motion is `VISUAL_ONLY` when no motor telemetry exists.

One Three.js unit represents one local metre. `TRUE SCALE` uses configured physical dimensions. `ICON SCALE` enlarges display geometry only so aircraft remain visible at wide zoom; calculations always use unscaled metre geometry. Current dimensions use the `SITL_REFERENCE_PROFILE` and remain `CONFIGURED_ASSUMPTION` until H1.

## Camera model

`CameraDigitalTwin` consumes image size, `fx/fy/cx/cy`, distortion model, `T_body_camera`, vehicle pose, and optional future mount pose. It computes `T_world_camera = T_world_body * T_body_camera`. Intrinsics unproject image pixels; extrinsics determine the camera mount. The frustum is an undistorted-model approximation. Corner rays are intersected independently with the selected ground model; invalid or above-horizon intersections are not drawn as a polygon.

Current ground state is `FLAT`. No online elevation or terrain service exists. A future local DEM adapter may provide offline terrain without changing the digital-twin contract.

## Performance and lifecycle

Three.js updates on `requestAnimationFrame`, while normalized domain snapshots arrive at a bounded rate. Trails are bounded/downsampled upstream. Geometry, materials, renderer resources, and controls are disposed when a view is removed, preventing leaks during repeated 2D/3D/Review switching.

The in-view performance panel reports frame rate, frame time, scene object count, and browser heap where the browser exposes it. Development-browser measurements are not Pi 4 performance claims.
