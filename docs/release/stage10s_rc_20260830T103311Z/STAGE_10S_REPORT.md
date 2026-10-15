# Stage 10S final software competition rehearsal

Software architecture: **FROZEN**  
Software release candidate: **PASS**  
Hardware Gate H1: **PENDING**  
Physical hardware touched: **NO**

All command-capable evidence in this report came from guarded loopback ArduCopter SITL. Targets/reacquisition and payloads were synthetic/simulated. Physical readiness and overall competition readiness remain **NO**.

## Scale qualification

| Item | Result |
|---|---|
| Near-limit boundary | 99,584.343 m² / 9.958434 ha |
| Global coverage segments | 39 |
| Planning determinism | PASS — identical global, fleet, and vehicle mission fingerprints |
| Partition imbalance | 4.3734% |
| Estimated fleet makespan | 331.225 s (software estimate) |
| Ten-survivor registry | PASS — 10 canonical targets, 10 cross-vehicle merges |
| Three-vehicle smoke | PASS — SYSIDs 1/2/3, three missions, command isolation |
| Process RSS growth during profiled planning | 1,994,752 bytes |
| Peak traced allocation | 661,980 bytes |

The exact near-limit geodesic area is below the configured 10 ha maximum. Self-intersections, zero area, duplicate vertices, out-of-range coordinates, excessive area, and margin-collapse inputs are rejected before mission execution.

## Software/SITL setup profile

| Path | P50 | P95 | MAX |
|---|---:|---:|---:|
| Import to plan ready | 56.083 ms | 63.193 ms | 65.118 ms |
| Import to fleet missions ready | 67.833 ms | 75.711 ms | 81.207 ms |
| Import to two-SITL heartbeat/upload/verify ready | 30.907 s | 33.749 s | 33.749 s |

These are development-host software/SITL measurements. Physical five-minute setup compliance is **NOT MEASURED**.

## Primary live competition rehearsal

Artifact: `data/fleet_runs/20260830T102139Z_competition/`

| Metric | Result |
|---|---|
| Fleet | 2 real ArduCopter SITLs, SYSIDs 1 and 2 |
| ArduPilot | ArduCopter 4.8.0, `af2a1bafc8ab993418e6714cbc2674dcc178d11a` |
| Endpoints | `tcp:127.0.0.1:5760`, `tcp:127.0.0.1:5770` |
| Operator mission starts | 1 |
| Manual interventions after START | 0 |
| Synthetic survivors | 4 |
| Canonical targets | 4 |
| Cross-vehicle merges | 4 |
| Verified simulated deliveries | 4 |
| Combined search coverage | 99.745254% |
| Drone 01 / Drone 02 coverage | 54.246316% / 69.383079% |
| Fleet distance | 669.670 m |
| Wall execution interval | 76.100 s at configured SITL speedup |
| Minimum observed horizontal / vertical separation | 1.325 m / 0.057 m |
| Critical separation events | 0 |
| Landing order | Drone 02, Drone 01 |
| Both disarmed | YES |
| Outcome | PASS / SUCCESS |

Mode evidence for Drone 01 was `AUTO → GUIDED → AUTO → GUIDED → AUTO → GUIDED → AUTO → GUIDED → AUTO → RTL`. Drone 02 remained in AUTO search while Drone 01 serviced targets, then entered RTL/LAND-DISARM. Simulation separation numbers are not physical safety thresholds.

## Qualification scenarios

| Scenario | Coverage | Deliveries | Outcome | Result |
|---|---:|---:|---|---|
| Deterministic baseline | 99.5% | 4/4 | SUCCESS | PASS |
| Recoverable subsystem failure | 99.2% | 4/4 | SUCCESS | PASS |
| Live vehicle loss/reallocation | 99.600465% combined | N/A | Safe recovery | PASS |
| Deadline limited | 96.0% | 0/4 | DEADLINE_LIMITED | PASS |
| Payload limited | 99.4% | 6/10 | CAPACITY_LIMITED | PASS |

Vehicle-loss artifact: `data/fleet_runs/20260830T102618Z_failure/`. Drone 02 followed simulated agent-abort RTL; Drone 01 received and completed the recovery mission. The failed vehicle was not forced back to AUTO.

The deadline manager recorded 200 s remaining against a conservative 587.5 s requirement and selected fleet recall. The payload-shortage scenario retained four unresolved confirmed targets with zero inventory violations and no phantom delivery.

## Safety, resilience, and recovery

- Stage 7S live rescue regression: PASS (78.17 s).
- Stage 8S live emergency-recall regression: PASS (51.89 s); no drop.
- Fleet one-action recall: PASS; both RTL, landed, and disarmed in 14.760 s.
- Deterministic resilience campaign: 22/22 PASS.
- Coordinator/backend snapshot, lease, checkpoint, delivered-target, database outbox, disk pressure, duplicate, ordering, stale-health, deadline, and payload idempotency tests: PASS.
- Physical endpoint and broadcast-command protections: PASS.
- Native ArduPilot GCS/C2, battery, EKF, and geofence injection: **NOT_TESTED** in this stage; normalized safety policies pass, but no native firmware result is fabricated.

## Competition mode and offline operation

- Mission start, safety abort, and emergency recall: ALLOWED and audited.
- Manual waypoint/goto/target tagging/target assignment/payload release/per-drone mode or RTL: REJECTED.
- Engineering APIs: DISABLED in competition mode.
- One GCS and one operator workflow: PASS.
- Static source/build offline compliance: PASS.
- Runtime socket audit: PASS; no unexpected external connection observed in the qualification process.
- WAN kill rehearsal: NOT_TESTED; source/build and runtime socket evidence are retained instead.
- Physical/live video: PENDING; UI state is explicitly simulated.

## Regression and build

- Python: **285 passed, 9 skipped**. Skips are physical H1 or separately opt-in SITL markers; required live campaigns above were run explicitly.
- Stage 10S focused frame/qualification/fleet/autonomy suite: 81 passed.
- Frontend: **15 passed**.
- Production frontend build: **PASS** (existing large-bundle warning is non-blocking).
- Offline compliance: PASS.

## Defects found and fixed

1. Multi-target live delivery initially used one-shot post-descent drop gating. It now maintains bounded horizontal and vertical correction and requires the complete existing gate to remain stable for the configured dwell; no threshold was relaxed.
2. Mission-boundary ENU and ArduPilot EKF `LOCAL_POSITION_NED` origins were treated as identical. Pose positions are now normalized to the explicit mission origin, and runner approach points use global-relative-altitude setpoints at the existing SITL navigator boundary.
3. The competition target fixture could confirm while AUTO was braking at an endpoint. Gold targets now use deterministic interior positions; boundary targets remain dedicated ineligibility/safety cases.
4. Multi-target frame IDs and assignment verification evidence were made target-specific/idempotent.

## Remaining limitations

Hardware Gate H1 is pending. Raspberry Pi 5, OV9281, Pixhawk/M8N, physical camera calibration/extrinsics/geolocation, aerial detection, payload, video, radio range, fleet links, failsafes, mass, endurance, launch-zone fit, and real separation remain unverified. See `PHYSICAL_VALIDATION_GAPS.md`.

Software ready: **YES**  
Physical ready: **NO**  
Competition ready: **NO**
