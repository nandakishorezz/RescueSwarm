# RescueSwarm software architecture freeze

Status: **FREEZE CANDIDATE — Stage 10S**

Hardware Gate H1: **PENDING**

This freeze applies to the architecture proven by deterministic tests and guarded ArduCopter SITL. It grants no physical command authority and provides no physical-aircraft evidence.

## Frozen decisions

- ArduCopter and `pymavlink` are the flight stack and protocol.
- Command-capable adapters accept numeric loopback SITL endpoints only. Stage 1/6 physical interfaces remain read-only.
- One authoritative MAVLink receive loop routes messages to telemetry, pose, mission protocol, acknowledgements, safety, and orchestration consumers.
- Raspberry Pi 5 is the intended airborne computer and Raspberry Pi 4 the intended ground computer; both remain pending H1 validation.
- The GCS remains offline React/FastAPI with the established local persistence and MapLibre architecture.
- `CameraSource` and `DetectorBackend` remain the acquisition/inference abstractions.
- Stage 5 calibration, frame transforms, synchronized pose history, ground intersection, target association, fusion, and uncertainty remain the geolocation architecture.
- `RescueMissionOrchestrator` remains authoritative for one vehicle's rescue workflow.
- `SafetySupervisor` and `RecoveryManager` remain authoritative for autonomy safety and state reconstruction while native ArduPilot failsafes retain higher flight-safety authority.
- `FleetMissionCoordinator` coordinates work ownership and delivery allocation without replacing vehicle agents.
- `FleetGateway` remains transport-neutral and local-only; no cloud coordination is permitted.
- Payload actions remain simulated and contain no servo, gripper, or GPIO command path.

## Changes allowed after H1

Measured configuration values, camera profiles, calibration registry entries, camera-to-body extrinsics, conservative uncertainty priors, physical read-only telemetry adapters, local link adapters, deployment unit details, and hardware-specific performance tuning may change when real evidence exists. Defect fixes may be made with focused regression tests.

## Changes requiring explicit reconsideration

Changing the autopilot/protocol, replacing the GCS or persistence architecture, introducing cloud dependencies, bypassing the single MAVLink reader, granting physical command authority, replacing the mission orchestrators, or adding a new fleet control hierarchy is outside this freeze and requires demonstrated need plus a new safety review.

## Evidence boundary

Stage 10S may establish `SOFTWARE_READY = PASS`. It cannot establish physical readiness or competition readiness. Those remain `NO` while Hardware Gate H1 and subsequent physical flight, payload, video, fleet-link, mass, endurance, and detection validations are incomplete.
