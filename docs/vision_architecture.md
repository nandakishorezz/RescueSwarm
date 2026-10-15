# Vision architecture

`CameraSource`, `VisionPreprocessor`, and `DetectorBackend` have no knowledge of Pixhawk, M8N, ArduCopter, mission planning, or target geography.

## Acquisition

`Picamera2CameraSource` is the single OV9281 owner. Replay sources never masquerade as physical hardware. Every source emits `FramePacket` with a deterministic source/sequence ID, monotonic capture time, wall time, optional sensor timestamp, dimensions, channel format, metadata, and queue-drop evidence.

`FrameProducer` is the only source reader. `LatestFrameQueue` has a fixed capacity and drops old frames. A slow detector therefore receives the freshest frame rather than a growing historical backlog. Capture FPS, detector input FPS, detector output FPS, frame age, and drops remain separate metrics.

## Pixel integrity

Monochrome arrays are replicated into equal R/G/B channels only where a three-channel model requires them. This does not create colour information. Letterbox scale and padding are retained per input. Tiled inputs also retain their original tile offset. Every backend result is restored, clipped, filtered, and NMS-merged into original captured-image pixels before creating a `Detection`.

Each detection retains center and bottom-center points plus pixel/normalized size. It contains no latitude, longitude, ENU position, vehicle pose, tracking identity, or geographic claim.

## Process isolation

`scripts/run_vision_agent.py` runs independently from future flight/mission processes. Its local output boundary is normalized `vision.frame_stats`, `vision.detection`, and `vision.health` events plus an atomic status artifact. A detector failure marks detector health while acquisition can continue. Camera recovery is bounded and backed off.

The GCS Engineering page reads status only. No live video transport, Redis, ROS, remote inference, or flight-control route was added.
