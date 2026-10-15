from __future__ import annotations

from ground.backend.app.domain.vehicle import VehicleState
from ground.backend.app.sitl.telemetry import SITLTelemetrySnapshot
from ground.backend.app.sitl.vehicle_source import SITLVehicleSource


class _Router:
    def subscribe(self, callback):
        self.callback = callback


def _snapshot(sequence: int) -> SITLTelemetrySnapshot:
    return SITLTelemetrySnapshot(
        vehicle=VehicleState(
            vehicle_id="drone_01", name="SITL", latitude=1, longitude=1,
            sequence=sequence,
        ),
        reached_items=list(range(sequence)),
    )


def test_vehicle_source_coalesces_backlog_to_latest_cumulative_snapshot() -> None:
    source = SITLVehicleSource(_Router())
    source.updates.put_nowait(_snapshot(1))
    source.updates.put_nowait(_snapshot(2))
    source.updates.put_nowait(_snapshot(3))

    update = source.next_update()

    assert update is not None
    assert update.vehicle.sequence == 3
    assert update.reached_items == [0, 1, 2]
    assert source.next_update() is None
