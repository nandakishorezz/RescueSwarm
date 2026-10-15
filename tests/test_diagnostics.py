import json
from pathlib import Path

from rescue_core.common.diagnostics import write_json


def test_write_json_handles_nested_diagnostic_data(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_json(destination, {"status": "not_tested", "reason": "No Pixhawk connection available"})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "reason": "No Pixhawk connection available",
        "status": "not_tested",
    }

