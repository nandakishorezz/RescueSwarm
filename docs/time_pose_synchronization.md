# Clock and vehicle-pose synchronization

Wall-clock UTC is retained for evidence, but latency alignment uses monotonic time. `ClockSyncEstimator` implements the MAVLink TIMESYNC midpoint estimate, records RTT, rejects excessive-RTT and robust offset outliers, and reports `UNSYNCED`, `WARMING_UP`, `SYNCED`, or `DEGRADED`.

`MAVLinkPoseSubscriber` attaches to `MAVLinkMessageRouter.subscribe()`. It never calls `recv_match()` and never opens another connection. It consumes `ATTITUDE_QUATERNION` preferentially, falls back to `ATTITUDE`, and combines `GLOBAL_POSITION_INT`, `LOCAL_POSITION_NED`, and `GPS_RAW_INT` without inventing missing accuracy.

`PoseHistory` is a configurable time-bounded ring history. Frame capture monotonic time is mapped to autopilot time, then position is linearly interpolated and attitude uses quaternion SLERP. It rejects empty histories, large interpolation gaps, far extrapolation, and out-of-order pose samples. Each result records both bracketing timestamps, fraction, nearest sample age, gap, extrapolation state, and clock quality.

The deterministic sensitivity fixture demonstrates a 4 m/s platform displaced by 0.4 m with a 100 ms stale pose and 0.8 m with 200 ms. These are mathematical fixture results, not measured aircraft performance.

