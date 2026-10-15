"""Raspberry Pi power/throttling evidence parsing."""

from __future__ import annotations

import re

from .models import EvidenceStatus, PowerHealth


_FLAGS = {
    0: "undervoltage_now",
    1: "frequency_capped_now",
    2: "throttled_now",
    3: "soft_temperature_limit_now",
    16: "undervoltage_occurred",
    17: "frequency_capping_occurred",
    18: "throttling_occurred",
    19: "soft_temperature_limit_occurred",
}


def parse_throttled_output(output: str | None) -> PowerHealth:
    """Parse ``vcgencmd get_throttled`` without treating absence as success."""

    match = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", output or "")
    if not match:
        return PowerHealth(
            status=EvidenceStatus.UNKNOWN,
            reasons=["vcgencmd throttling value unavailable"],
        )
    value = int(match.group(1), 0)
    values = {name: bool(value & (1 << bit)) for bit, name in _FLAGS.items()}
    current = [name for name in _FLAGS.values() if name.endswith("_now") and values[name]]
    historic = [name for name in _FLAGS.values() if name.endswith("_occurred") and values[name]]
    reasons: list[str] = []
    if current:
        reasons.append("current unstable power/thermal condition: " + ", ".join(current))
    if historic:
        reasons.append("historical power/thermal condition recorded: " + ", ".join(historic))
    if not reasons:
        reasons.append("no Raspberry Pi power/throttling flags set")
    return PowerHealth(
        raw_value=value,
        status=EvidenceStatus.FAILED if current else EvidenceStatus.VERIFIED,
        valid_for_precision_measurement=not current,
        reasons=reasons,
        **values,
    )
