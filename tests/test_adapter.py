from unittest.mock import Mock

import pytest

from rescue_core.adapters.autopilot.ardupilot_mavlink import ArduPilotMavlinkAdapter
from rescue_core.adapters.autopilot.base import Stage1SafetyError


class FakeMessage:
    def __init__(self, message_type: str, **fields: object) -> None:
        self._type = message_type
        self._fields = fields

    def get_type(self) -> str:
        return self._type

    def to_dict(self) -> dict[str, object]:
        return {"mavpackettype": self._type, **self._fields}


def test_adapter_parses_mocked_gps_message() -> None:
    adapter = ArduPilotMavlinkAdapter("mock")
    adapter.master = Mock()
    adapter.master.recv_match.return_value = FakeMessage(
        "GPS_RAW_INT", fix_type=3, satellites_visible=10, eph=99, epv=120,
        lat=130000000, lon=770000000, alt=900000,
    )
    gps = adapter.get_gps()
    assert gps is not None
    assert gps["fix_type"] == 3
    assert gps["latitude_deg"] == pytest.approx(13.0)
    adapter.master.recv_match.assert_called_once_with(
        type="GPS_RAW_INT", blocking=True, timeout=2.0
    )


@pytest.mark.parametrize(
    "method", ["arm", "takeoff", "upload_mission", "goto", "rtl", "land", "release_payload"]
)
def test_stage1_write_operations_are_blocked(method: str) -> None:
    adapter = ArduPilotMavlinkAdapter("mock")
    with pytest.raises(Stage1SafetyError, match="disabled in Stage 1"):
        getattr(adapter, method)()

