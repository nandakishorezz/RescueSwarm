# Camera profiles

`CameraProfile` contains resolution, FPS, intrinsics, distortion, rigid BODY-FRD extrinsics, exposure, latency, and noise labels. The bundled OV9281 profile is a simulation reference; its physical calibration and mount remain unverified.

When `fx/fy/cx/cy` exist, they are authoritative and FOV is derived. Gazebo `horizontal_fov`, the Stage 11 digital-twin frustum, and tests use the same profile.

The builder converts Stage 5 optical axes (`x right, y down, z forward`) to the Gazebo camera sensor axes (`x forward, y left, z up`) and BODY FRD to Gazebo FLU in one helper. Camera world pose is always vehicle pose multiplied by mount extrinsics. Wind has no camera-specific offset.

Phase 1 verifies a real Gazebo camera topic at 1280×800 and measures 30.303 Hz in Gazebo simulation time for the configured 30 Hz sensor. Wall-clock publication is slower under the current non-accelerated LLVMPipe renderer, so camera-rate qualification uses simulation timestamps. Frames are observer evidence only; no detector is connected.
