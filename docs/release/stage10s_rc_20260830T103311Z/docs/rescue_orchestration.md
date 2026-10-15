# Rescue orchestration architecture

`RescueMissionOrchestrator` owns the explicit rescue state graph. It cannot open MAVLink. `SITLRescueMissionRunner` performs transport actions through the already guarded `SITLAutopilotAdapter`; `SITLGuidedNavigator` centralizes MAVLink coordinate frames and setpoint masks. The existing Stage 3 router remains the only `recv_match()` owner.

Target service is accepted only when confirmation, uncertainty, operational-region containment, payload inventory, vehicle health, battery, and deadline reserve pass. The queue is target-ID deduplicated and first-confirmed by default.

The default resume strategy is `REPLAY_FROM_LAST_REACHED`. It may repeat part of a leg, but it does not skip the interrupted survey section. The chosen checkpoint, `MIS_RESTART` diagnostic value, requested sequence, and observed `MISSION_CURRENT` are persisted.

GUIDED arrival and drop readiness are telemetry gates, not command acknowledgements. Close alignment uses a bounded proportional velocity controller. Velocity commands are refreshed locally, and a watchdog treats stale command output as unsafe. Broad reacquisition is a bounded cross pattern inside the operational region.

All values in `config/rescue_orchestration.yaml` are SITL engineering defaults, not physical-flight approval.
