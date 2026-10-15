# Competition-mode validation

Competition Mode locks the operator surface after mission start. Allowed categories are `MISSION_START`, `SAFETY_ABORT`, and `EMERGENCY_RECALL`. Manual waypoint, goto, target confirmation/deletion, payload drop, resume sequence, flight mode, and flight-path adjustment are rejected and audited.

Engineering simulation/failure APIs are unavailable in competition configuration. Browser or WebSocket loss does not stop onboard AUTO; reconnect receives an authoritative snapshot and stale telemetry expires instead of remaining green.

`CompetitionReadinessValidator` reports each category as PASS, FAIL, PENDING, or NOT_APPLICABLE. The current overall result is deliberately **NOT COMPETITION READY** because:

- Hardware Gate H1 is pending;
- physical failsafes and payload are unverified;
- real aerial survivor recall and live video are unverified;
- a two-or-more-drone fleet is not implemented;
- combined mass and launch/landing footprint are not measured physically.

SITL evidence never satisfies a physical-evidence field. The offline compliance scan remains the source/build network gate.
