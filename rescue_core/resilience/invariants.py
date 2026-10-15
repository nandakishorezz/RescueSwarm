"""Runtime-checkable mission invariants that fail closed."""

from __future__ import annotations

from typing import Any

from .models import InvariantCheck


class MissionInvariantChecker:
    def check(self, state: dict[str, Any]) -> list[InvariantCheck]:
        checks: list[InvariantCheck] = []
        target_state = state.get("target_state")
        verification = bool(state.get("delivery_verification_succeeded"))
        checks.append(InvariantCheck(
            name="delivered_requires_verification",
            passed=target_state != "DELIVERED" or verification,
            detail="A DELIVERED target must have successful verification evidence",
        ))
        rescue_state, mode = state.get("rescue_state"), str(state.get("vehicle_mode", "UNKNOWN")).upper()
        checks.append(InvariantCheck(
            name="guided_requires_actual_guided",
            passed=rescue_state not in {"GUIDED_APPROACH", "REACQUIRING", "PRECISION_ALIGNING", "DROP_READY", "DELIVERING"} or mode == "GUIDED",
            detail="Target-service states require actual GUIDED mode evidence",
        ))
        checks.append(InvariantCheck(
            name="searching_requires_auto",
            passed=rescue_state != "SEARCHING" or mode == "AUTO",
            detail="SEARCHING requires AUTO evidence",
        ))
        remaining = int(state.get("payload_remaining", 0))
        checks.append(InvariantCheck(
            name="payload_nonnegative", passed=remaining >= 0,
            detail="Payload inventory cannot become negative",
        ))
        slot_targets = [item.get("target_id") for item in state.get("payload_slots", []) if item.get("target_id")]
        checks.append(InvariantCheck(
            name="payload_slot_unique_target", passed=len(slot_targets) == len(set(slot_targets)),
            detail="One target cannot own two payload slots",
        ))
        attempts = [item for item in state.get("delivery_attempts", []) if item.get("state") not in {"VERIFIED", "FAILED", "UNKNOWN"}]
        active_targets = [item.get("target_id") for item in attempts]
        checks.append(InvariantCheck(
            name="single_active_attempt_per_target", passed=len(active_targets) == len(set(active_targets)),
            detail="One target cannot have two active delivery attempts",
        ))
        failsafe = mode in {"RTL", "LAND", "SMART_RTL"}
        checks.append(InvariantCheck(
            name="native_failsafe_not_overridden",
            passed=not (failsafe and bool(state.get("autonomy_mode_override_requested"))),
            detail="Native failsafe mode cannot be silently overridden",
        ))
        checks.append(InvariantCheck(
            name="target_geolocation_required",
            passed=rescue_state not in {"APPROACH_PLANNING", "GUIDED_APPROACH"} or bool(state.get("target_geolocation_valid")),
            detail="GUIDED approach requires valid geolocation",
        ))
        checks.append(InvariantCheck(
            name="sitl_command_authority_only",
            passed=state.get("command_connection", "tcp:127.0.0.1:5760").startswith(("tcp:127.0.0.1:", "udp:127.0.0.1:", "udpin:127.0.0.1:")),
            detail="Command-capable resilience path accepts only loopback SITL",
        ))
        return checks

    def require(self, state: dict[str, Any]) -> list[InvariantCheck]:
        checks = self.check(state)
        failed = [item.name for item in checks if not item.passed]
        if failed:
            raise RuntimeError("MISSION_INVARIANT_VIOLATION: " + ",".join(failed))
        return checks
