"""Conservative state reconciliation after backend or orchestrator restart."""

from __future__ import annotations

from .models import MissionRecoverySnapshot, RecoveryDisposition, RecoveryResult


class RecoveryManager:
    def reconcile(
        self, snapshot: MissionRecoverySnapshot, *, actual_mode: str,
        actual_armed: bool, link_fresh: bool, mission_sequence: int,
    ) -> RecoveryResult:
        mode = actual_mode.upper()
        evidence = {
            "persisted_state": snapshot.orchestrator_state,
            "persisted_mode": snapshot.last_confirmed_vehicle_mode,
            "actual_mode": mode,
            "actual_armed": actual_armed,
            "actual_sequence": mission_sequence,
        }
        if snapshot.execution_source != "SITL":
            return RecoveryResult(
                disposition=RecoveryDisposition.FAILED_SAFE, reconstructed_state="FAILED",
                reason="Stage 8S recovery refuses non-SITL command authority", evidence=evidence,
            )
        if not link_fresh:
            return RecoveryResult(
                disposition=RecoveryDisposition.FAILED_SAFE, reconstructed_state="FAILED",
                reason="Actual vehicle state unavailable; delivery cannot resume", evidence=evidence,
            )
        if mode in {"RTL", "SMART_RTL"}:
            return RecoveryResult(
                disposition=RecoveryDisposition.OBSERVE_RETURNING, reconstructed_state="RETURNING",
                reason="Actual autopilot failsafe/RTL is authoritative", evidence=evidence,
            )
        if mode == "LAND" or not actual_armed:
            return RecoveryResult(
                disposition=RecoveryDisposition.OBSERVE_LANDING, reconstructed_state="LANDING",
                reason="Actual landing/disarmed state is authoritative", evidence=evidence,
            )
        if mode == "AUTO":
            return RecoveryResult(
                disposition=RecoveryDisposition.RECONSTRUCT_AUTO, reconstructed_state="SEARCHING",
                reason="Onboard AUTO continued; rebuild telemetry and coverage from actual sequence",
                evidence=evidence,
            )
        if mode == "GUIDED":
            return RecoveryResult(
                disposition=RecoveryDisposition.REQUEST_RTL, reconstructed_state="ABORTING",
                reason="Companion restart during GUIDED is ambiguous; never resume delivery blindly",
                evidence=evidence,
            )
        return RecoveryResult(
            disposition=RecoveryDisposition.REQUEST_RTL, reconstructed_state="ABORTING",
            reason=f"Persisted and actual state cannot be safely reconciled in mode {mode}", evidence=evidence,
        )
