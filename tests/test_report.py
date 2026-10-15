from pathlib import Path

from tools.generate_stage1_report import generate_report


def test_report_without_evidence_is_not_tested() -> None:
    report = generate_report(None)
    assert "| MAVLink heartbeat | NOT_TESTED |" in report
    assert "| Camera enumerated | NOT_TESTED |" in report
    assert "hardware diagnostics have not been run" in report


def test_empty_run_does_not_become_success(tmp_path: Path) -> None:
    report = generate_report(tmp_path)
    assert "VERIFIED" not in "\n".join(
        line for line in report.splitlines() if line.startswith("| ") and "Status" not in line
    )
