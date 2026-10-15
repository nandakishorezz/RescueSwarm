# Failure injection

`config/resilience_scenarios.yaml` is the machine-readable fault matrix. Each scenario defines an ID, phase, injection method, expected safety action, safe final state, criticality, and recovery timeout. Seeded transport profiles model latency, jitter, and drops without flaky tests.

Normalized injection changes only domain health/evidence. It is used when modifying ArduPilot sensors would be inappropriate or unavailable. Native SITL tests must say so explicitly and record the ArduPilot parameters and mode/STATUSTEXT evidence. Any parameter experiment belongs to a fresh SITL instance; `SITLScenarioParameterSet` rejects non-loopback endpoints and records original/scenario/restored values.

Engineering endpoints under `/api/engineering/failures/` return 403 when competition mode is active. The failure injector has no physical adapter dependency.

Critical campaign scenarios include control-link loss during GUIDED, critical battery/EKF/geofence evidence, emergency recall, target loss, payload verification failure, orchestrator restart during AUTO, manual-drop rejection, and physical-endpoint rejection. A critical failure cannot be hidden by an aggregate percentage.
