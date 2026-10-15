import json

from rescue_core.common.models import DiagnosticResult, EvidenceStatus


def test_diagnostic_result_serializes_status_and_time() -> None:
    result = DiagnosticResult(
        name="heartbeat", status=EvidenceStatus.NOT_TESTED, reason="No hardware attached"
    )
    encoded = json.dumps(result.json_safe())
    assert '"status": "NOT_TESTED"' in encoded
    assert "collected_at" in result.json_safe()

