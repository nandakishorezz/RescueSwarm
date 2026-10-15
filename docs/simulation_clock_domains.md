# Simulation Clock Domains

Phase 2B status: source-time integration implemented; combined mapping remains
NOT QUALIFIED. No live frame-time geolocation is accepted while the combined
mapping gate is invalid.

`rescue_core/geolocation/clock_sync.py` defines immutable integer `Timestamp`
values and explicit domains: GAZEBO_SIM, HOST_MONOTONIC, BRIDGE_MONOTONIC,
ARDUPILOT_BOOT, ARDUPILOT_TIMESYNC, WALL_CLOCK. Subtraction across domains fails.
The extra BRIDGE_MONOTONIC domain is necessary: WSL and Windows clocks cannot
be assumed to have the same epoch or rate.

## Verified Sources

Installed `gz.msgs10.image_pb2.Image` has a Header; live capture evidence records
`header.stamp.sec` and `header.stamp.nsec`. Example from qualification C:
4 seconds + 522000000 nanoseconds = 4522000000 GAZEBO_SIM ns. The image bridge
now drops messages without an explicit header stamp rather than using receipt
time. Zero is a valid present simulation timestamp, not a missing timestamp.

Installed `gz.msgs10.clock_pb2.Clock` has `sim`, `real`, `system` time fields.
The new clock-only bridge subscribes to
`/world/rescueswarm_stage13sim/clock`; it never reads perfect vehicle pose.
It keeps at most 256 clock samples and serves bounded local probe responses.
Clock timestamps are decimated to 10 ms source-time spacing before fitting.

ArduPilot source was inspected read-only in the installed WSL tree:
`libraries/GCS_MAVLink/GCS_Common.cpp:3773-3779,3793-3849`.
TIMESYNC replies contain `timesync_receive_timestamp_ns()`, using the UART
receive-time constraint or `AP_HAL::micros64()`, multiplied by 1000.
`libraries/SITL/SIM_JSON.cpp:505-526` advances AP simulation time from received
physics timestamp deltas. This does NOT establish identical Gazebo/AP epochs.
No ArduPilot/plugin source or firmware was modified.

## Mapping Method

Existing `ClockSyncEstimator` retains its offset-mode default for prior stages.
Explicit `affine=True` enables a bounded, centered weighted least-squares fit of
TIMESYNC midpoint host time to AP TIMESYNC time. RTT filtering remains enabled;
the clock qualification rejects RTT over 20 host ms. A MAD gate excludes fit
outliers, but residual metrics include ALL samples, not just fitted inliers.
TIMESYNC fits use a two-second host window. Other fits use five seconds.
Anchor arithmetic avoids fitting directly against large absolute nanoseconds.

The clock diagnostic chain is:

1. Probe Linux bridge monotonic against Windows host monotonic using send/receive
   midpoint and RTT interval width.
2. Map Gazebo clock callback receipt into Windows host time, then fit sim to host.
3. Fit repeated routed TIMESYNC replies from host midpoint to AP TIMESYNC.
4. Compare GLOBAL_POSITION_INT source `time_boot_ms` against mapped AP TIMESYNC
   at telemetry arrival. This is a diagnostic correspondence with arrival bias,
   NOT independently verified AP boot alignment.

Half RTT is a conservative interval contribution under probe assumptions. It is
not a statistical standard deviation. Fits expose slope, offset, anchored
equation, P50/P95/max residual, age, version, epoch and sample count.
The combined mapper never labels unknown one-way delay as zero uncertainty.
It currently always reports `autonomy_mapping_permitted: false`.

## Source-Time Integration (2026-09-07)

`FramePacket.capture_timestamp_sim_ns` carries the original integer
`gz.msgs.Image.header.stamp` through the detector result. Host monotonic time
remains separate bridge/receipt telemetry and is not promoted to capture time.
The existing single MAVLink router feeds source-timestamped `time_boot_ms`
samples and TIMESYNC replies to one mapper. Stage-5 uses strict autopilot-time
lookup with linear position interpolation and quaternion SLERP only after a
qualified mapping; there is no receipt-time fallback.

## Live Qualification C

Run: `stage13sim_phase2b_clock_20260907c`.
911 live images, 30.030 simulation seconds, 89.639 host seconds; disarmed.
758 accepted TIMESYNC replies, 251 rejected; rolling retained count 100.
Rolling TIMESYNC RTT P50/P95: 5.7263 / 17.07617 HOST_MONOTONIC ms.

Latest windows, not campaign-wide percentiles:

| Fit | Slope | Residual P50 | Residual P95 | Max residual | Quality |
| --- | --- | --- | --- | --- | --- |
| Gazebo sim -> host | 2.8238987404 | 11.683731 host ms | 32.307652 host ms | 38.494960 host ms | VALID for diagnostic 100-host-ms gate only |
| Host -> AP TIMESYNC | 0.3547939106 | 2.994030 AP ms | 7.974801 AP ms | 8.546712 AP ms | VALID local fit |
| WSL bridge -> host | 1.1111109163 | 0.033716 host ms | 0.285263 host ms | 1.389791 host ms | VALID local fit |
| AP boot -> AP TIMESYNC | unavailable | unavailable | unavailable | unavailable | CONVERGING, 8 retained samples |

The local Gazebo diagnostic threshold is NOT the 16.7-ms gold error threshold.
There was no combined VALID state and no effective-error qualification.
Final local TIMESYNC uncertainty contribution was 15.880851 AP ms before other
legs and unknown one-way bias. Do not report this as full capture-time accuracy.

All raw probes, source timestamps and mapping windows stream to
`clock_samples.jsonl`. Summary: `CLOCK_MAPPING_EVIDENCE.json`. Runs A/B/C are
preserved independently. A exposed the 1-kHz-window bug; B/C improve sampling
but do not qualify frame-time pose. These are clock-only runs, not gold runs.

## Remaining Blocker

The fresh disarmed run `stage13sim_phase2b_clock_sync_20260907d` captured 608
header-stamped images over 20.031 simulation seconds, but the final combined
mapping was INVALID: AP boot-to-TIMESYNC was INVALID, host-to-AP was DEGRADED,
and Gazebo-to-host residual P95/max were 29.172/44.128 ms. The required
effective capture-time bound remains unqualified. Independent motion
validation is not yet performed.
No truth pose, receipt-time geolocation, TIMESYNC fault injection, or physical
hardware was used. Pause/resume and live reboot/reset controls remain untested.
