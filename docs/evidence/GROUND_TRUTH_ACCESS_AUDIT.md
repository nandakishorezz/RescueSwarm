# Ground Truth Access Audit — Phase 2E

Date: 2026-09-09. Overall: **PASS**.

The transitive runtime surface was inspected through imports, constructors,
repository contracts, API serialization, and the recorded Phase-2E execution.
The live gold run is `stage13sim_phase2e_gold_20260909a`.

## Allowed truth authorities

| Authority | Use | Result |
|---|---|---|
| `SimulationGroundTruth` / simulation orchestration | Build hidden worlds and segregated observer artifacts | PASS |
| `WorldBuilder` | Place hidden models and evaluator labels in Gazebo | PASS |
| `SimulationObserver` | Bounded vehicle/wind observation | PASS |
| `SimulationEvaluator` | Post-inference segmentation and error scoring | PASS |
| Evaluation/report scripts and truth-specific tests | Judge-only comparison | PASS |

## Autonomy surface

| Component | Inputs inspected | Ground-truth access |
|---|---|---|
| `GazeboCameraSource` | image transport header + rendered pixels | NONE beyond pixels |
| Detector backend / `VisionAgent` | `FramePacket` | NONE |
| confirmation / `SpatiotemporalTargetTracker` | Stage-5 observations | NONE |
| Stage-5 geolocation | MAVLink EKF pose, camera profile, YOLO bbox, flat-ground model | NONE |
| Coverage planner | mission boundary and search configuration | NONE |
| Mission service / repositories / normal Mission API | mission, vehicle, target, event DTOs | NONE |
| Fleet coordinator / delivery allocator | confirmed target and vehicle DTOs | NONE |

AST import checks cover vision, simulation perception, geolocation, autonomy,
planning, fleet, services, repositories, domain, and SITL. None imports
`rescue_core.simulation.truth` or `rescue_core.simulation.evaluator`. Hidden
survivor displacement leaves the pre-detection mission fingerprint unchanged.
Normal simulation and mission API tests exclude truth fields; observer endpoints
require explicit opt-in and are disabled in competition mode.

The source-time sidecar schema carries only run/message identity and simulation
time. Its schema guard rejects pose, position, velocity, attitude, geographic,
survivor, bbox, segmentation, and truth fields. The live gold run used MAVLink
EKF pose values and reports `gazebo_truth_pose_used=false` and
`host_time_used_for_pose_selection=false`.

## Causality

`EVALUATOR_CAUSALITY_TEST.json` records identical autonomy fingerprints with
the evaluator branch enabled and disabled. `TRUTH_OVERLAY_CAUSALITY.json`
records no mission-plan, vehicle-path, detector, confirmation, or target change
when observer display state changes. Evaluation consumes segmentation only
after inference and cannot suppress the fully occluded control or the gold
run's false-positive detections.

Legacy synthetic Stage-7 fixtures remain isolated from the Phase-2 gold CLI.
They are not imported by the pixel pipeline and none of their synthetic boxes,
visibility radii, or target truth appears in Phase-2E evidence.

Final verdict: **PASS — zero ground-truth dataflow into Phase-2 autonomy.**

## Phase 3 sensor boundary extension

Phase 3 deliberately permits Gazebo and ArduPilot SITL sensor backends to
consume rigid-body world state in order to emit simulated GPS, accelerometer,
gyro, magnetometer, barometer, and camera measurements. This is the
**WORLD/SENSOR SIMULATION SIDE** of the firewall. `sensor_realism.parm` is
loaded only by the disposable SITL process and uses `SIM_*` measurement-layer
parameters before EKF3; it contains no target or mission-state field.

The **AUTONOMY SIDE** still receives only MAVLink EKF estimates, rendered
MONO8 frames, detections, and the timing-only pose witness. Camera realism
operates on rendered pixels and immutable capture metadata; it has no world
pose or survivor API. `EstimatorRealismEvaluator`-equivalent analysis in
`scripts/analyze_stage13sim_phase3.py` reads segregated truth only after/on the
observer branch and never publishes its results to mission, geolocation,
confirmation, or target services. Raw sensor diagnostics are exposed only by
the opt-in `/api/simulation/observer/truth` endpoint.

The normal status/API path continues to exclude world truth and raw evaluator
error. AST/import boundaries remain unchanged for detector, VisionAgent,
TargetManager/tracker, Stage-5 geolocation, planner, mission coordinator,
fleet allocation, and normal mission API.

Phase-3 verdict: **PASS — sensor models use truth on the simulation side only;
autonomy direct truth access remains zero.**

## Phase 4 target-service and payload boundary extension

The Phase-4 live controller in `ground/backend/app/sitl/phase4_service.py`
consumes only confirmed Stage-5 estimates, current source-time EKF pose, and
new pixel-derived observations. Its coarse approach, bounded reacquisition,
principal-ray alignment, release gate, and search-resume selection have no
simulation-truth or evaluator import. Vehicle motion uses MAVLink GUIDED
setpoints. The payload adapter sends `MAV_CMD_DO_SET_SERVO` through ArduPilot
before publishing to the Gazebo detachable-joint adapter and has no impact or
survivor-truth input.

Gazebo world construction legitimately consumes payload geometry and vehicle
state on the simulation-physics side. The payload trajectory, impact point,
and survivor miss distance are read only by post-run finalization and remain
explicitly labeled `EVALUATOR_TRUTH`; the live mission records that the
physical delivery score is unknown to autonomy.

The live single-survivor run
`stage13sim_phase4_single_gold_20260909m` records `truth_used=false` for the
approach, source `GAZEBO_RENDERED_YOLO_STAGE5` for reacquisition/alignment, and
an actuator/mechanism-only release acknowledgement. The 3.692 m evaluator
impact miss was not fed back into mission state.

Phase-4 truth-firewall verdict for the implemented single-vehicle path:
**PASS — no truth-assisted route, approach, reacquisition, alignment, release,
or resume dataflow.** Full-rescue evaluator/overlay causality pairs and the
two-vehicle Phase-4 path remain unqualified, so this audit does not by itself
constitute an overall Phase-4 pass.
