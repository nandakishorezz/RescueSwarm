# MAVLink 5760 Wire Time Origin — Root Cause & Qualification (Phase-2C)

Status: RESOLVED — clean-capture runs prove the pose stream is byte-identical
to the arducopter TXSOCK frames (case A). Explanation below.

## 1. Question being answered

`MAVLinkPoseSubscriber` reads `GLOBAL_POSITION_INT.time_boot_ms` (plus
`LOCAL_POSITION_NED`/`ATTITUDE`/`ATTITUDE_QUATERNION`) over
`tcp://127.0.0.1:5760`, which hops Windows → wslrelay → WSL arducopter socket,
then to the pose stream on SERIAL0. `GLOBAL_POSITION_INT` is msgid **33**.

Evidence runs 039/040 showed pose-boot readings ~40.5 s **ahead of** the
simultaneously captured in-process GCS probe and the lowest-socket write trace
(TXSOCK on the SERIAL0 accepted socket), which both showed fresh sim-domain
boots (e.g. 6320→). That contradiction had to be classified:

- A. producer = serialized = transport = received (pose gets exactly what
  arducopter writes; no wire transform), and 039/040 were an artifact of how the
  run harness sampled the pose.
- B. the pose connection received a different, transformed stream (hidden
  second source, wslrelay rewrite, virtual clock).

## 2. Instrumentation used

- **TXSOCK trace** (`UARTDriver.cpp` hooks at `send(:886)`, `::write(:914)`,
  `send(_fd,:937)`, after `handle_writing_from_writebuffer_to_device`): logs the
  raw MAVLink2 frame head (`stx,len,seq,sysid,compid`), boot read from the frame
  payload (`f_boot`), and a WSL host timestamp for the SERIAL0 accepted socket
  (fd=13), the single socket the pose is connected to.
- **In-process GCS probe** (`GCS_Common.cpp` at the producer): fresh sim boots.
- **Raw frame parse on the pose side**: `get_msgbuf()` + header decode yields
  `f_seq/f_sysid/f_compid/f_boot` per received frame, compared against the
  trace's per-frame `seq`/`boot`.
- **ss / netstat snapshots** on both sides (WSL `ss -tlnp -p`, Windows
  `netstat -ano`) to enumerate every 5760 listener and every accepted
  connection; plus a leftover-process probe (`wsl_probe.sh`).

Hard rule honored throughout: no second live consumer was ever attached to the
pose's accepted connection besides the pose itself.

## 3. Results by run (full numbers in
`data/simulation_runs/MAVLINK_WIRE_ORIGIN_EVIDENCE.jsonl`)

| run | pose | pose first boot | trace/gcs first boot | head gap (ms) | pose `boot==f_boot` | verdict |
|---|---|---|---|---|---|---|
| 039 | 109 | 46853 | 6253 | +40600 | not parsed (old fmt) | trace overflowed (19/514 ok); pose elevated |
| 040 | 109 | 46821 | 6320 | +40501 | not parsed (old fmt) | pose elevated, trace/gcs sim-clean |
| 041 | 0 | – | 6362 | – | – | pose capture bug (`get_header()` None → callback raised); no rows |
| 042 | – | – | – | – | – | run script crash (`witness_rows` NameError); no file |
| 043 | 500 | 4901 | 4901 | **0** | **500/500** | clean, byte-identical |
| 044 | 500 | 4733 | 4733 | **0** | **500/500** | clean, byte-identical |

Runs 043/044 (hardened run script, see §5):

- Pose `boot` equals the raw-frame payload `f_boot` for **500/500** rows.
- Pose `f_seq`/`f_boot`/`f_sysid`/`f_compid` pairs match the TXSOCK trace
  exactly where the pose's message type is the traced one (e.g. seq 42,48,52,62,
  67,72,76,81 ↔ boots 4901,4986,5086,5186,5286,5386,5486,5586). The pose keeps a
  `max()` across four message types, so non-traced message types legitimately
  occupy part of the pose row stream.
- Pose boots start at the *same* value the trace/gcs started at (head gap = 0),
  i.e. the pose's first reading is the first sim-domain frame, no offset.
- Single `LISTEN 0.0.0.0:5760` (fd=12) + single accepted `ESTAB …5760 fd=13`
  during capture, on both 043 and 044. Windows side: one wslrelay (PID 304496)
  LISTENING 127.0.0.1:5760 with one ESTABLISHED pose connection.

**Conclusion: case A.** In the clean, single-consumer, early-anchored state the
pose receives the exact frames arducopter writes on SERIAL0. There is no hidden
second stream and no wire-level clock transform.

## 4. Root cause of the 039/040 elevation

**Confirmed: the elevation is real, and it is a clock-domain property, not a
stale-cache artifact.** MAVLink fields carry `time_boot_ms = AP_HAL::millis()`,
which under SITL resolves to GCS-thread `AP_HAL::micros64()`:
`AP_HAL_SITL/system.cpp` returns `_stopped_clock_usec` when the stopped-clock
latch is visible to that thread, otherwise it falls back to **wall time since
process start**. Under fast-sim execution the GCS thread does not see the latch
and its `time_boot_ms` becomes wall-anchored — biased ~+40-48 s ahead of
gazebo and advancing at wall rate, i.e. slower than sim time and **bursty**
(stalls under host load / GCS-thread scheduling). The SIM-loop
`ap_time_now_us`/`micros64` inside FDM processing tracks gazebo 1:1
(slope 0.99967, residual 0.2-0.5 ms). Two independent ArduPilot time domains
therefore exist simultaneously:

| domain | source | anchored to | vs gazebo |
| --- | --- | --- | --- |
| in-loop `ap_time_now_us` | SIM_JSON `time_now_us` | stopped sim clock | affine 1:1, VALID |
| MAVLink `time_boot_ms` | GCS-thread `micros64()` | wall (fallback) | non-affine, bursty |

Supporting evidence:
- GCS-side probe rows (`gcs_gpi`) show `time_boot_ms == millis == micros64 ==
  stopped_clock_*` on the wire; the pose side reads exactly those bytes
  (probe-c: at one host instant GPI/LPN/ATT/ATTQ `boot == gz` to the ms —
  no per-message bias, byte-identity on the wire).
- requal `POSE_DOMAIN_PROBE` offsets start ~+48.3 s and converge toward +43 s;
  an affine fit of boot↔gz reads **DEGRADED** with slope ≈ 2.6-2.7 and
  p95 residuals ≈ 2-2.6 s — grossly non-linear, i.e. the wall-fallback clock is
  not a fixed affine function of gazebo.
- Slow-sim runs (≈1×) look aligned (probe-c: boot 43.612 s at gz 43.618 s,
  −6 ms) because wall ≈ sim; the bias appears only when the sim runs faster
  than wall.
- 039/040 numbers are thereby explained exactly: a +40.5 s offset advancing
  slower than gz matches a wall-anchored GCS boot clock under ~4× fast sim —
  not a stale cache, and not a wire transform. Bytes remain identical end-to-end.

## 5. Fix that removed the anomaly (applied to the run harness)

1. **Subscribe the pose callback before `wait_ready` / before any capture**, so
   no sim-time elapses between pose connection and capture start.
2. **Anchor rows to boot**: capture begins by polling the router's latest GPI
   immediately (tag `src=early`) and drains in a 50 ms poll loop, so the first
   persistent pose row carries the boot at connection time, not ~40 s later.
3. **Enumerate the wire**: per-run ss snapshots (single listener + single
   accepted socket) and raw-frame parse on the pose side allow the
   `boot==f_boot` and `seq↔trace` identity checks that make elevation
   detectable.
4. **Startup hygiene guard** (implemented in the requal harness): kill leftover
   `sim_vehicle.py`/`arducopter`/`generated_world.sdf`, then verify exactly one
   `LISTEN 0.0.0.0:5760` and a single `arducopter` binary before launch
   (`WIRING_GUARD.json` `passed: true` gates every qualifying run).
5. **Two-domain pose mapping for frame sync** (historical diagnostic): because MAVLink
   `time_boot_ms` is a bursty wall clock, frame pose lookups can NOT use an
   affine boot↔gz map. Frames (gazebo domain) are mapped to `time_boot_ms` by
   interpolating through the shared host monotonic clock — witness rows give
   gz↔host, pose subscriber gives host↔boot — then looked up in the boot-keyed
   `PoseHistory` (30 s boot window). requal run `stage13sim_phase2c_requal_20260908g`:
   direct clock VALID (slope 0.99967, p95 0.5 ms), 28 frame-pose sync rows,
   0 rejections, 0 pre-sync skips, exit code 0. The affine pose-domain witness
   is kept as a diagnostic and reads DEGRADED. It is not autonomy-authoritative.
6. **Simulation-source pose timestamp** (Phase-2C final): SITL-only
   instrumentation emits timing/identity sidecars at the estimator telemetry
   serialization points. The receive-side join uses exact
   `(channel, sysid, compid, msgid, MAVLink sequence, time_boot_ms identity)`;
   `time_boot_ms` disambiguates sequence wrap but is never the pose-time key.
   The sidecar contains no position, attitude, velocity, or truth. Run
   `stage13sim_pose_source_time_20260909d` joined 2,688/2,688 records with no
   duplicates or out-of-order identities and keyed 506 PoseHistory samples in
   `ARDUPILOT_SIM_SOURCE`. Controlled rendered-pixel run
   `stage13sim_phase2_source_time_controlled_20260909a` used that path for
   51 Stage-5 observations and one confirmed/geolocated target; host time and
   Gazebo truth pose were not used for pose selection.

## 6. Forward guidance

- DirectSimClockWitness and DirectSimCaptureClockMapper qualify on clean,
  boot-anchored runs (head gap = 0, `boot==f_boot` per frame, single 5760
  listener). Use the 043/044 procedure for all future qualification.
- Do not attach a second live consumer to fd=13 / TCP 5760 while qualifying.
- Do not fit boot↔gz affine maps in fast-sim runs under this SITL variant;
  the MAVLink clock is wall-fallback and bursty. The host-clock two-track
  interpolation from §5.5 is diagnostic only. Authoritative SITL_GAZEBO frame
  lookup is Gazebo capture time → direct simulation clock →
  `ARDUPILOT_SIM_SOURCE`-keyed PoseHistory.

## 7. Artifacts

- `data/simulation_runs/MAVLINK_WIRE_ORIGIN_EVIDENCE.jsonl` — per-run record.
- `data/simulation_runs/wire_trace_039/..044/` — `WIRE_TRACE_RUN.json`,
  `pose_rows.json`, `mavlink_wire_trace.jsonl`, `arducopter.log`.
- `C:\Users\nanda\AppData\Local\Temp\opencode\run_wire_trace.py` — hardened
  single-run capture used for 043/044.
- `C:\Users\nanda\AppData\Local\Temp\opencode\wsl_probe.sh` — leftover
  listener/process probe.
