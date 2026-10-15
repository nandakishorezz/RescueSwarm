# STAGE 11S REPORT

## Verdict

**STAGE 11S COMPLETE — SOFTWARE UI / DIGITAL TWIN**

- Hardware Gate H1: **PENDING**
- Physical hardware touched: **NO**
- Evidence source: existing SITL telemetry, persisted mission evidence, synthetic camera fixtures, and software-only UI tests
- Stage 10 release artifacts: preserved; Stage 11 evidence is stored separately under `data/stage11s_runs/`

## GCS

| Mode | Result | Evidence |
|---|---|---|
| SETUP | PASS | Mission import details, readiness, fleet/payload summary, 2D/3D preview, one START action |
| MISSION | PASS | Dominant synchronized spatial view, fleet/target panels, status/alerts, competition-safe actions |
| ENGINEERING | PASS | Spatial inspector, transform matrices, geometry simulator, health freshness, ray/measurement tools |
| REVIEW | PASS | Timestamp replay, event seeking, 0.25x–4x rates, local report links |

The served 1920×1080 application was opened in headless Chrome and captured successfully. No browser runtime asset failed to load.

## 3D digital twin

| Capability | Result |
|---|---|
| MapLibre custom Three.js layer | PASS |
| Standalone/split Three.js view | PASS |
| Actual ENU position and altitude | PASS |
| Quaternion body attitude | PASS |
| Procedural aircraft/rotors/body axes | PASS |
| Planned/search/diversion trajectories | PASS |
| Sectors, targets, uncertainty, launch zone | PASS |
| True-scale vs display-icon scale separation | PASS |
| Renderer resource disposal | PASS |

Vehicle dimensions are the explicitly labeled `SITL_REFERENCE_PROFILE`; physical dimensions are not verified.

## Camera digital twin

- Pose: `T_world_camera = T_world_body × T_body_camera`
- Intrinsics: Stage 5-compatible pinhole/Brown and fisheye unprojection
- Extrinsics: fixed-mount model with optional future mount abstraction
- Frustum: calibrated corner rays; undistorted-model approximation
- Footprint: guarded ray/flat-ground intersections in 2D and 3D
- Observation: selected pixel, camera/world ray, backend target point, discrepancy
- Physical calibration: **PENDING**

## Coordinate-frame gate

| Frame/operation | Result |
|---|---|
| CAMERA OPTICAL | PASS |
| BODY FRD | PASS |
| NED | PASS |
| ENU | PASS |
| WGS84/local display conversion | PASS |
| Quaternion SLERP | PASS |
| Python/TypeScript shared fixture | PASS |

Maximum shared-fixture discrepancy: **0.000e+00 m**.

The backend Stage 5 result remains authoritative. Frontend reconstruction is visualization/diagnostics only and cannot overwrite target coordinates.

## Fleet and targets

- Multiple vehicle identity, pose, state, trajectory, sector, assignment, payload, and freshness views: PASS
- Combined plan/coverage and guided-diversion distinction: PASS
- Ten-target and two-vehicle operator-state rendering test: PASS
- Candidate/confirmed/assigned/approaching/delivering/delivered/unserviceable/ambiguous styling: PASS
- Detector confidence and position uncertainty shown separately: PASS
- Geographic uncertainty circles/disks: PASS
- Observation evidence and cross-vehicle provenance: PASS

## Replay

- Persisted timestamp source: PASS
- Deterministic playback: PASS
- Position interpolation and quaternion SLERP: PASS
- Stale-gap rejection: PASS
- Mission-event seek and step: PASS
- Synchronized 3D state: PASS

An existing Stage 3 ArduCopter SITL run was loaded through the new replay endpoint and contains real persisted SITL pose/mode/event history. No new physical or live multi-SITL campaign was claimed for Stage 11.

## Competition mode

- One mission start: PASS
- Safety abort: ALLOWED
- Emergency recall: ALLOWED
- Manual flight/target/payload controls: REJECTED by existing policies
- Engineering controls/APIs: DISABLED by existing competition policy
- Persistent simulation/H1 provenance badges: PASS

## Performance and bounded state

- Production render/build with 2-vehicle/10-target data model: PASS
- RequestAnimationFrame FPS/frame-time/object/heap instrumentation: PASS
- Bounded event and trail stores: PASS (regression tests)
- No React processing of raw MAVLink packets: PASS
- WebSocket normalized state model retained: PASS
- Measured Pi 4 browser performance: **NOT MEASURED**
- Quantitative WebGL FPS: exposed live in the UI; headless browser GPU timing is not reported as physical/operator-machine evidence

## Verification

- Python: **289 passed, 9 skipped**
- Skips: physical Pi/camera/Pixhawk tests and opt-in live SITL campaigns only
- Frontend: **26 passed** across 6 files
- Focused Stage 11 Python/API/spatial gate: PASS
- TypeScript production compilation: PASS
- Vite production build: PASS (local bundle-size warning remains non-blocking)
- Offline compliance: **PASS**, zero violations
- Browser render at 1920×1080: PASS

## Physical values still pending

- Vehicle dimensions and rotor radius: CONFIGURED / UNVERIFIED
- OV9281 intrinsics, sensor mode, and real FOV: PHYSICAL_UNVERIFIED
- Camera extrinsics/mount offsets: PHYSICAL_UNVERIFIED
- Motor telemetry: UNKNOWN (rotor motion is VISUAL_ONLY in SITL)
- Terrain: FLAT configured model; no local DEM loaded
- Physical live video: NOT AVAILABLE

## Problems discovered and fixed

1. Legacy `GLOBAL_POSITION_INT` fixtures lacked optional absolute-altitude/vertical-speed fields. The additive normalizer now preserves old messages and fills new fields only when supplied.
2. The production TypeScript compiler found a malformed footprint-layer ternary not exercised by unit transpilation. It was corrected and the production build rerun.

## Known limitations

1. The offline map currently uses the intentional local blank style unless local map data is provisioned; mission geometry and all operational overlays remain available offline.
2. Pi 4 browser FPS/memory and real camera/vehicle geometry cannot be qualified until the deployment hardware is available.
3. The large locally bundled Three.js/MapLibre application chunk produces a non-blocking Vite size warning; route-level lazy loading already limits Mission/Engineering entry chunks.

## Evidence

- `data/stage11s_runs/stage11s_20260905T050929Z/stage11s_qualification.json`
- `data/stage11s_runs/stage11s_20260905T050929Z/stage11s_qualification_report.md`
- `data/stage11s_runs/stage11s_20260905T050929Z/gcs_setup_1920x1080.png`
- `OFFLINE_COMPLIANCE_REPORT_STAGE11S.md`

## Status

```text
SOFTWARE UI / DIGITAL TWIN: COMPLETE
HARDWARE GATE H1: PENDING
```

Only genuine UI defects, replay validation, local-terrain preparation, and measured hardware integration should change this layer. The autonomy architecture remains frozen.
