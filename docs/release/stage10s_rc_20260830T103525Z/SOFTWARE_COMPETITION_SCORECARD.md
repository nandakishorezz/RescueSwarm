# Software competition scorecard

Evidence scope: **SOFTWARE + ARDUCOPTER SITL**  
Hardware Gate H1: **PENDING**

| Category | Result | Evidence |
|---|---|---|
| Mission input | PASS | Offline GeoJSON/KML validation, adversarial and deterministic malformed-input tests |
| Area validation | PASS | Near-limit 9.958434 ha fixture accepted; over-limit and invalid geometry rejected |
| Planning | PASS | Deterministic global/fleet/per-vehicle fingerprints; 39-segment near-limit plan |
| Fleet coordination | PASS | Two live ArduCopter SITLs, one coordinator and one mission start |
| Parallel search | PASS | Independent AUTO missions with geometric combined coverage |
| Target registry | PASS | Ten-target scale and cross-vehicle canonical deduplication |
| Geolocation pipeline | PASS | Stage 5 mathematical/SITL evidence; physical accuracy pending |
| Delivery allocation | PASS | Eligibility, leases, payload accounting, reassignment and exactly-once rules |
| Delivery orchestration | PASS | Live SITL GUIDED service; synthetic reacquisition and simulated payload only |
| Failure handling | PASS | Deterministic campaigns plus live recall and vehicle-failure recovery |
| Fleet recall | PASS | One action, per-vehicle RTL evidence, landing and disarm |
| Landing | PASS | Coordinated landing gate and terminal-state checks |
| Single GCS | PASS | One normalized fleet interface; no per-drone mission-control application |
| Offline operation | PASS | Source/build scan and runtime socket classification |
| Operator restrictions | PASS | Only START, SAFETY_ABORT and EMERGENCY_RECALL allowed in competition mode |
| Evidence logging | PASS | Hashed plans/config/source, events, telemetry, reports and release manifest |
| Setup performance | PASS — SOFTWARE/SITL | P50/P95 measured; physical five-minute result not measured |

Software release candidate: **PASS**  
Physical readiness: **NO**  
Competition readiness: **NO**
