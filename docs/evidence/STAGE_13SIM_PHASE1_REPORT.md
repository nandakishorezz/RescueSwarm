# STAGE 13SIM — PHASE 1 COMPLETE

## Architecture

The frozen RescueSwarm autonomy is surrounded by a separate simulation laboratory. The live causal path is `Gazebo Harmonic rigid-body physics and sensors → official ardupilot_gazebo plugin → ArduCopter SITL → the existing loopback-only pymavlink router → RescueSwarm/GCS`. `SimulationManager` validates and freezes configuration, generates local SDF, owns processes, checks readiness, and records evidence; it does not control the aircraft.

Perfect pose, wind, and survivor state use separate `WorldTruth*` DTOs in the simulation truth namespace. Only the world builder, observer, evaluator, and simulation tests consume them. Normal mission/status APIs expose MAVLink estimates only. No camera frame was connected to a detector in Phase 1.

Conventional truth artifact filenames are non-truth redirect records. The actual payload is present only in files prefixed `SIMULATION_ONLY_GROUND_TRUTH`, preventing an ambiguous file from becoming a future ingestion shortcut.

Hardware Gate H1: **PENDING**

Physical hardware touched: **NO**

## Environment

| Item | Evidence |
|---|---|
| OS | Windows 11 host; Ubuntu 24.04.3 LTS guest |
| WSL/native | WSL2, kernel 6.6.87.1-microsoft-standard-WSL2 |
| Gazebo | LIVE — Gazebo Harmonic |
| Gazebo version | gz-sim 8.15.0 |
| ardupilot_gazebo | LIVE — official current integration at `/home/nandakishore7/ardupilot_gazebo` |
| ardupilot_gazebo commit | `082a0fe231f6e63bc8d1598f1cba461d9e2ea7f5` |
| ArduPilot | ArduCopter 4.8.0 DEV, LIVE SITL |
| ArduPilot commit | `af2a1bafc8ab993418e6714cbc2674dcc178d11a` |
| OpenGL/rendering | OGRE2; Mesa 25.2.8 LLVMPipe, direct rendering, **not hardware accelerated** |
| GStreamer | Runtime libraries present; `gst-launch-1.0` CLI not installed. Gazebo Transport camera evidence does not depend on it. |

The exact discovery output is in `data/simulation/environment_manifest.json`.

## Simulation Mode

| Capability | Result |
|---|---|
| UI | PASS |
| Drone Builder | PASS — Basic/Advanced, validation, provenance, generated rotor layout |
| Camera Builder | PASS — intrinsics/FOV, mount transform, latency/noise, digital-twin preview |
| Environment | PASS — deterministic calm/constant/turbulent/gusting profiles |
| Mission upload | PASS — existing mission importer/planner and verified MISSION_ITEM_INT transfer |
| Survivor placement | PASS — add/move/delete before start; configuration locks while running |
| 3D preview | PASS — vehicle, rotors, camera/frustum/footprint, boundary, launch zone, observer survivors |
| Responsive acceptance | PASS — 1366×768, 1536×864, 1920×1080, and effective 125% zoom; zero horizontal overflow |

## Drone Profile

| Field | Value |
|---|---|
| Frame | QUAD_X reference profile |
| Rotor count | 4; four generated motor joints, positions, axes, and spin directions |
| Mass | 1.5 kg — `REFERENCE_PRESET`, not measured hardware |
| Geometry provenance | `REFERENCE_PRESET` / `ENGINEERING_ESTIMATE` |
| Generated SDF | PASS — physics/collision and visual geometry are distinct |

Motor outputs apply forces through Gazebo. RescueSwarm never derives attitude from PWM.

## Camera Profile

| Field | Value |
|---|---|
| Sensor | OV9281 reference simulation — **not physically calibrated** |
| Resolution | 1280×800 mono |
| FPS | Configured 30 Hz; measured 30.303 Hz in Gazebo simulation time |
| Intrinsics source | fx=fy=800 px, cx=640 px, cy=400 px — `ENGINEERING_ESTIMATE` |
| Derived FOV | horizontal 1.349482 rad; vertical 0.927295 rad |
| Mount | translation (0, 0, 0.08) m BODY-FRD and camera-to-body quaternion (0.707107, 0, 0, 0.707107) — `PHYSICAL_UNVERIFIED` |
| Gazebo camera | PASS — real sensor topic, matching resolution/rate/FOV |
| Digital-twin consistency | PASS — Gazebo SDF and GCS derive from the same `CameraProfile` |

The pixel-only observer test renders an actual procedural survivor in FOV, removes it when placed outside FOV, and produces distinct images under 20° camera pitch and 15° roll. Mean absolute image differences are 0.5744, 1.2700, and 1.2078 intensity units respectively. No detector was connected.

## Mission Georeference

| Field | Evidence |
|---|---|
| Boundary | `small_easy.geojson`, 3,999.371 m² (0.399937 ha) |
| WGS84 origin | 17.45022586971622, 78.38037651796802, 585 m |
| ENU ↔ Gazebo | PASS — Mission E/N/U equals Gazebo X/Y/Z through one explicit transform |
| Maximum tested round-trip error | 2.072×10⁻⁹ m through WGS84; 0 m through ENU↔Gazebo |

Boundary markings, the 3.6576 m square launch zone, vehicle, and survivor objects use this same georeference.

## Hidden Survivors

| Check | Result |
|---|---|
| Placed | 3 (standing, lying, standing) |
| Spawned in Gazebo | PASS — visible procedural world models |
| Visible to observer | YES, only behind explicit observer-truth access |
| Visible to autonomy as coordinates | NO |
| Targets created before pixel detection | 0 |
| Ground-truth leakage tests | PASS |

Competition Mode returns 403 for all simulation routes. Normal mission/status snapshots and target APIs contain no survivor truth. Import-boundary tests prohibit frozen autonomy, vision, fleet, planning, and SITL packages from importing `simulation.truth`.

## Wind

| Field | Value |
|---|---|
| Model | Gazebo Harmonic `WindEffects`; vehicle links opt into physical wind |
| Mean speed | 3.0 m/s |
| Direction | 90° from north |
| Turbulence | enabled, 0.35 m/s sigma, ±8° direction variation |
| Gusts | enabled, up to 7.0 m/s for 1.5–3.0 s |
| Seed | 84521 |
| Deterministic | PASS — same profile/seed reproduces schedule, different seed differs |
| Physical-force evidence | PASS — calm/gust controlled runs use identical route/profile except wind and produce different rigid-body motion/controller response |

Wind truth remains observer-only; no perfect wind value is sent to autonomy.

## Wind / Attitude Coupling

| Metric | Calm control | Gust run |
|---|---:|---:|
| Airborne roll peak-to-peak | 33.586° | 60.526° |
| Airborne pitch peak-to-peak | 44.688° | 60.123° |
| Motor output range | 1330–1670 PWM | 1328–1689 PWM |
| Same 114.992 m route simulation duration | 99.3 s | 150.4 s |

The gust run therefore adds 26.941° roll variation, 15.435° pitch variation, and 51.1 simulation seconds to the same controlled route. Position/velocity disturbance and ArduPilot motor compensation are present in independently recorded Gazebo truth and MAVLink estimate streams. These figures characterize the simulation reference model, not a physical aircraft.

## Camera / Attitude Coupling

| Check | Result |
|---|---|
| Camera world pose follows vehicle | PASS |
| Wind changed camera quaternion | PASS — camera transform is recomputed from truth vehicle pose × fixed extrinsics |
| Footprint displacement | maximum relative excursion 17.977 m in gust run versus 13.878 m calm; +4.099 m |
| No manual wind offset | PASS — no `wind_offset`, display shake, pose teleport, or frustum mutation path exists |

The recorded maximum camera quaternion change is 179.984° across the full AUTO/RTL route and includes commanded yaw/turns; it is not presented as gust-only rotation.

## Gold Live Gazebo Mission

Run: `stage13sim_phase1_gold_final_20260906x`

| Assertion | Result |
|---|---|
| Takeoff | PASS — airborne state observed from MAVLink |
| AUTO | PASS — real mode history and exact final mission sequence reached |
| Requested speed | 4.0 m/s |
| Measured speed | 4.343 m/s P95; 4.847 m/s maximum (includes vehicle/controller dynamics) |
| Camera active | PASS — frames captured at simulation times 18.687 s and 56.562 s |
| Wind/gust | PASS |
| RTL | PASS — commanded only after exact final search sequence |
| Landing | PASS — sustained MAVLink ground kinematics after real RTL |
| Disarmed | PASS |

Mission evidence: 114.992 m planned route, 1,505 Gazebo truth samples, 1,295 MAVLink estimate samples, 150.4 s simulation time, 160.795 s wall time, and 0.935 real-time factor. A separate equal 39.996 m speed test completed in 91.3 s at a 2 m/s request and 76.2 s at a 5 m/s request; P95 speed increased from 3.13 to 4.28 m/s.

The generated reference model's external-simulator landed-state field can remain unknown. The runner permits a final disarm only under `simulation_only` authority and only after an observed flight, actual RTL, relative altitude <0.15 m, |vertical speed| <0.12 m/s, horizontal speed <0.25 m/s, sustained for eight seconds. Physical authority cannot call this path.

## Truth Comparison

| Evidence | Result |
|---|---|
| Gazebo truth tracked | PASS — 1,505 observer-only samples |
| ArduPilot estimate tracked | PASS — 1,295 MAVLink samples |
| Position discrepancy captured | PASS — median 0.440 m, maximum 1.457 m |
| Attitude discrepancy captured | PASS — median 1.247°, maximum 21.933° |

The discrepancy is evaluation evidence from asynchronously sampled streams, not a physical sensor-accuracy claim.

## Tests

| Suite | Result |
|---|---|
| Simulation unit/API/import-boundary | 10 passed |
| Simulation integration/evidence | PASS — final gust gold, calm control, two speed runs, four camera cases |
| Gazebo live | PASS — real physics, camera, ArduPilot AUTO/RTL/landing/disarm |
| Frontend | 34 passed across 8 files, including 2 Simulation page tests |
| Previous regression | PASS; full Python suite includes Stages 1–12U |
| Total automated pytest/vitest | 340 passed; 9 intentional physical/opt-in SITL skips |

Build: **PASS**. Vite production build completed. The existing lazy MapLibre chunk warning remains non-blocking; no remote asset was introduced.

Offline runtime compliance: **PASS**. Source, build, runtime config, simulator profiles, and assets remain local. Package installation during development is separate from runtime.

## Artifacts

- `data/simulation/environment_manifest.json`
- `data/simulation_runs/stage13sim_phase1_gold_final_20260906x/`
- `data/simulation_runs/stage13sim_phase1_calm_20260906u/`
- `data/simulation_runs/stage13sim_phase1_speed2_20260906v/`
- `data/simulation_runs/stage13sim_phase1_speed5_20260906w/`
- `data/simulation_camera_tests/camera_visual_test.json`
- `data/stage13sim_phase1_ui/browser_acceptance.json`
- `data/simulation/plugin_build.log`

## Files

- `rescue_core/simulation/`
- `simulation/profiles/`
- `ground/backend/app/api/simulation.py`
- `ground/frontend/src/pages/SimulationPage.tsx`
- `ground/frontend/src/components/SimulationWorldPreview.tsx`
- `scripts/run_stage13sim_phase1.py`
- `scripts/run_stage13sim_camera_visual_tests.py`
- `tests/test_stage13sim_phase1.py`
- `docs/simulation_*.md`

## Problems discovered

1. Killing the Windows WSL relay alone can leave its Linux Gazebo child alive. Simulation process ownership now records and terminates the exact Linux server PID; the camera test also isolates each case with a unique Gazebo partition.
2. The simplified generated reference model may not publish a reliable `EXTENDED_SYS_STATE` landed value after physically settling. The test runner uses the narrow simulation-only, eight-second kinematic landing guard described above; it cannot be used through physical authority.

## Known limitations

1. All vehicle mass/inertia, motor/prop curves, drag, camera optics/mount, sensor dynamics, and wind coefficients are reference/estimated simulation values. They are not calibrated to hardware.
2. Rendering uses non-accelerated LLVMPipe and the GStreamer CLI is absent. Phase 1 camera evidence works via Gazebo Transport; Phase 2 must choose and qualify a bounded local frame transport before detector integration.

## Result

PHASE 1: **PASS**

READY FOR PHASE 2: **YES**

Phase 2 is **not implemented** by this stage. Its authorized next boundary is pixel-only virtual perception: Gazebo-rendered frames → existing `CameraSource` → existing `DetectorBackend`, with zero ground-truth shortcut.

Hardware Gate H1 remains **PENDING**.
