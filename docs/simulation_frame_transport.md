# Simulation Frame Transport

Selected transport: Python `gz.transport13.Node.subscribe` with
`gz.msgs10.Image` inside Ubuntu WSL, sending a length-prefixed JSON header and
binary image bytes over loopback TCP to Windows. Python bindings are available;
neither ROS, GStreamer nor a native C++ bridge is necessary.

One producer subscribes to the autonomy image. Preview and detection overlay
read post-inference snapshots. A separate evaluator-only source subscribes to
the matched semantic camera. There is no per-frame CLI subprocess. CLI topic
listing is used only during startup discovery.

## Bounds And Integrity

- Bridge deque: 3 by default, configurable only to 2-4; drop oldest/latest read.
- Vision queue: 2; drop oldest/latest read; observed high-water depth recorded.
- Header limit: 65,536 bytes.
- Payload is checked against required dimensions, pixel format and row stride
  before receiving image bytes; SHA256 verifies integrity.
- Source sequence must increase; repeated capture timestamps and backward
  simulation time fail explicitly. A reset requires a new run epoch.
- Identical image checksums are counted separately from duplicate identities.
  A stationary camera can legitimately produce identical pixels.
- Stop interrupts socket reads before joining the producer. Startup timeout
  cleanup is reentrant. Transient Windows status-file reader locks do not stop
  inference and have an explicit failure counter.

## Timing Blocker

The Gazebo capture timestamp and Linux bridge-receive timestamp are preserved.
The existing canonical host capture field is still a receipt-time proxy, labeled
`UNVERIFIED_RECEIPT_TIME_PROXY`. Capture age is null, not zero. Geolocation
rejects these frames. This is NOT a completed timestamp-preservation contract.

Before enabling geolocation, establish and validate the mapping among Gazebo
capture time, ArduPilot boot time, Linux monotonic time and Windows monotonic
time across variable real-time factor. Do not infer the mapping from survivor
coordinates, image truth boxes, perfect vehicle pose, or detector success.

## Measured Run

Final recovery run: 99.859 simulation seconds; 3,028 bridge callbacks generated,
3,027 frames received, 1 bridge drop, 271 vision drops, 2,755 inferred, maximum
queue depth 2. Received simulation-time rate 30.303 Hz, wall receive rate
5.544 Hz. Camera-interval RTF 0.182948. No checksum, duplicate timestamp,
out-of-order identity or clock-reset errors.

The generated count is callbacks observed by the bridge, not independently
measured frames before subscription. TCP buffers are additional buffering not
covered by the deque bound. Full capture age, bridge latency and long-duration
memory boundedness remain unqualified. Event/observation history still grows
with run duration even though image queues and monitor files are bounded.
