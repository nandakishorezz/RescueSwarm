# Mission recovery

`MissionRecoverySnapshot` persists only the minimum state required to reconcile a restart: mission fingerprint/source, orchestrator state, current target, search checkpoint, inventory, delivery attempts, delivered targets, mission sequence, coverage, last mode, and timestamp.

On restart, actual vehicle mode and armed/link state are authoritative:

- actual AUTO reconstructs SEARCHING and telemetry/coverage tracking;
- actual RTL/SMART_RTL reconstructs RETURNING and is never overridden;
- actual LAND/disarmed reconstructs LANDING;
- actual GUIDED after companion restart is ambiguous, closes the delivery gate, and requests RTL;
- stale link, non-SITL authority, or irreconcilable state fails safe and never resumes delivery.

Delivery attempts use an idempotency token and ordered states. `RELEASED` after restart is not re-released, and a target becomes `DELIVERED` only after verification. Duplicate events are ignored by event ID. Search checkpoints and delivered target IDs survive restart.

If SQL persistence is unavailable, critical events enter a bounded local NDJSON outbox. Once the database returns they replay idempotently. Under disk pressure, optional images and diagnostics are rejected before mission or critical evidence.
