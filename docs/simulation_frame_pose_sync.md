# Simulation Frame Pose Synchronization

Status: BLOCKED at clock qualification. Stage-5 source-time lookup is now
connected to the diagnostic affine mapper, but combined timing quality is not
VALID and no live frame-time observation is accepted.

GazeboCameraSource preserves image capture sim ns and separate Linux/Windows
receipt ns. Its legacy FramePacket host capture field remains an explicitly
unverified receipt proxy. No consumer may promote that field to frame-time
geolocation. `capture_pose_alignment_verified` stays false and the pipeline
rejects observations with CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED.

The existing PoseHistory retains source autopilot boot ns and host receipt ns,
with bounded duration, linear position interpolation and quaternion SLERP. The
strict source-time lookup requires valid mapping quality, correct source time,
surrounding samples and a bounded interpolation gap. No receipt-time, latest
telemetry, or perfect Gazebo-pose fallback is used by Phase 2.

The current MAVLink subscriber can combine cached message components with
different source timestamps. Component freshness/coherence, synchronized
history mutation, reboot clearing and strict bracket validation remain pending.
Source timestamp, mapped boot timestamp, mapping version/uncertainty and pose
diagnostics must then propagate to observations and replay without replacing
capture time with detector completion time.

CLOCK_MAPPING_EVIDENCE.json exists for three new disarmed qualification runs.
FRAME_POSE_SYNC_EVIDENCE.jsonl and PIXEL_TO_CONFIRMED_GEOLOCATION_PROOF.json are
not created: no qualified frame-time pose or target exists in Phase 2B yet.
Creating empty success-looking artifacts would misrepresent the gate result.

Capture-to-receive/detector/geolocation latency remains UNKNOWN until a valid
mapping exists. Compute latency remains HOST_MONOTONIC. Fit residuals are in
the destination domain and must not be called actual frame age.
