"""Actual MAVLink-backed vehicle source; not the Stage 2 event simulator."""

from __future__ import annotations

import queue
from typing import Any

from ..simulation.vehicle_source import VehicleSource
from .telemetry import SITLTelemetryNormalizer, SITLTelemetrySnapshot


class SITLVehicleSource(VehicleSource):
    def __init__(self, router: Any, vehicle_id: str = "drone_01", max_updates: int = 200):
        self.router = router
        self.normalizer = SITLTelemetryNormalizer(vehicle_id)
        self.updates: queue.Queue[SITLTelemetrySnapshot] = queue.Queue(maxsize=max_updates)
        self.router.subscribe(self._on_message)

    def _on_message(self, message: Any) -> None:
        snapshot = self.normalizer.handle(message)
        if message.get_type() not in {
            "HEARTBEAT", "GLOBAL_POSITION_INT", "GPS_RAW_INT", "ATTITUDE", "SYS_STATUS",
            "MISSION_CURRENT", "MISSION_ITEM_REACHED", "EXTENDED_SYS_STATE", "STATUSTEXT",
        }:
            return
        if self.updates.full():
            try:
                self.updates.get_nowait()
            except queue.Empty:
                pass
        self.updates.put_nowait(snapshot)

    def reset(self) -> None:
        self.normalizer = SITLTelemetryNormalizer(self.normalizer.snapshot.vehicle.vehicle_id)
        while not self.updates.empty():
            try:
                self.updates.get_nowait()
            except queue.Empty:
                break

    def next_update(self) -> SITLTelemetrySnapshot | None:
        try:
            latest = self.updates.get_nowait()
        except queue.Empty:
            return None
        # Every normalized snapshot is a complete, cumulative read model
        # (including reached_items).  Mission control needs the newest state,
        # not up to 200 historical copies.  Returning FIFO here made the live
        # runner about 18 seconds stale during RTL and caused its landing guard
        # to expire while the actual vehicle was still descending normally.
        while True:
            try:
                latest = self.updates.get_nowait()
            except queue.Empty:
                return latest
