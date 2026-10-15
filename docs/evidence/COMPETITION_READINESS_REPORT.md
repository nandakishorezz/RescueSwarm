# Competition readiness

Hardware Gate H1: **PENDING**  
Overall: **NOT COMPETITION READY**

| Requirement | Software status | SITL evidence | Physical evidence | Final status |
|---|---|---|---|---|
| Mission input and <=10 ha validation | PASS | Deterministic scale qualification | NONE | PASS — software |
| Single-drone autonomy | PASS | Live Stage 7S regression | NONE | PASS — SITL |
| Multi-drone coordination | PASS | Two-vehicle live fleet rehearsal | NONE | PASS — SITL |
| Area division and parallel scouting | PASS | Partition and concurrent AUTO evidence | NONE | PASS — SITL |
| Shared target registry | PASS | Cross-vehicle merge and ten-target scale | NONE | PASS — SITL/software |
| Delivery allocation | PASS | Lease-based allocation and simulated delivery | NONE | PASS — SITL/software |
| Failure recovery | PASS | Deterministic campaign and live failure/recall evidence | NONE | PASS — SITL/software |
| Competition operator lock | PASS | Forbidden-action regression | NONE | PASS — software |
| One GCS | PASS | Unified fleet read model | NONE | PASS — software |
| Offline runtime architecture | PASS | Static/build scan and socket audit | NONE | PASS — software |
| Hardware prototype | PENDING | N/A | NONE | PENDING — H1 |
| Real aerial survivor detection | PENDING | Synthetic only | NONE | PENDING |
| Real video | PENDING | Simulated state only | NONE | PENDING |
| Physical payload/release verification | PENDING | Simulated only | NONE | PENDING |
| Physical failsafes | PENDING | SITL only | NONE | PENDING |
| Physical fleet and link | PENDING | Localhost SITL only | NONE | PENDING |
| Combined all-up weight <=25 kg | PENDING | N/A | NOT MEASURED | PENDING |
| 12 ft x 12 ft launch/landing fit | PENDING | Reference positions only | NOT TESTED | PENDING |

Software autonomy, multi-drone coordination, competition mode, and offline compliance are release-candidate qualified. Physical readiness remains **NO** and competition readiness remains **NO** until H1 and subsequent physical evidence are complete.
