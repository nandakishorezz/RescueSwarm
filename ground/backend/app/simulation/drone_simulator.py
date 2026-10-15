"""Deterministic GCS event simulator. This is explicitly not flight/SITL simulation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from shapely.geometry import Point, Polygon

from ..domain.events import EventSeverity, EventType, MissionEvent
from ..domain.mission import Mission, MissionState
from ..domain.target import DeliveryState, Target, TargetState
from ..domain.vehicle import VehicleState
from ..services.coordinates import LocalCoordinateFrame
from .vehicle_source import VehicleSource


@dataclass
class SimulatorUpdate:
    vehicle: VehicleState
    mission_state: MissionState
    elapsed_seconds: float
    coverage_percent: float
    events: list[MissionEvent] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)


class SimulatedVehicleSource(VehicleSource):
    """Seeded, deterministic UI event source using a synthetic path inside a polygon."""

    def __init__(self, mission: Mission, seed: int = 42, total_steps: int = 40):
        if mission.boundary is None:
            raise ValueError("Simulator requires an imported mission boundary")
        self.mission = mission
        self.seed = seed
        self.total_steps = total_steps
        self.random = random.Random(seed)
        self.frame = LocalCoordinateFrame(mission.boundary.origin)
        self.path = self._mock_path()
        self.step_index = 0
        self.target: Target | None = None
        self.paused = False
        self.low_battery = False
        self.gps_degraded = False
        self.link_lost = False

    def _mock_path(self) -> list[tuple[float, float]]:
        vertices = [(point.longitude, point.latitude) for point in self.mission.boundary.vertices]
        polygon = Polygon(vertices)
        center = polygon.representative_point()
        anchors = [center]
        for longitude, latitude in vertices[:8]:
            candidate = Point(
                center.x + (longitude - center.x) * 0.55,
                center.y + (latitude - center.y) * 0.55,
            )
            anchors.append(candidate if polygon.covers(candidate) else center)
        anchors.extend(reversed(anchors[1:-1]))
        points: list[tuple[float, float]] = []
        for index in range(self.total_steps):
            progress = index / max(1, self.total_steps - 1)
            scaled = progress * (len(anchors) - 1)
            segment = min(int(scaled), len(anchors) - 2)
            fraction = scaled - segment
            start, end = anchors[segment], anchors[segment + 1]
            points.append((start.x + (end.x - start.x) * fraction, start.y + (end.y - start.y) * fraction))
        return points

    def reset(self) -> None:
        self.step_index = 0
        self.target = None
        self.paused = False
        self.low_battery = False
        self.gps_degraded = False
        self.link_lost = False
        self.random = random.Random(self.seed)

    def next_update(self) -> SimulatorUpdate | None:
        if self.paused:
            return None
        if self.step_index >= self.total_steps:
            return None
        step = self.step_index
        self.step_index += 1
        longitude, latitude = self.path[step]
        progress = step / max(1, self.total_steps - 1)
        next_point = self.path[min(step + 1, self.total_steps - 1)]
        heading = (math.degrees(math.atan2(next_point[0] - longitude, next_point[1] - latitude)) + 360) % 360
        battery = max(8 if self.low_battery else 25, 100 - progress * 68)
        mission_state = MissionState.RUNNING
        if step == self.total_steps - 2:
            mission_state = MissionState.RETURNING
        elif step == self.total_steps - 1:
            mission_state = MissionState.COMPLETED
        vehicle = VehicleState(
            vehicle_id="drone_01",
            name="RescueSwarm Simulator 01",
            connected=not self.link_lost,
            latitude=latitude,
            longitude=longitude,
            relative_altitude_m=0 if step in {0, self.total_steps - 1} else 20,
            roll_deg=math.sin(step / 4) * 2,
            pitch_deg=math.cos(step / 5) * 1.5,
            yaw_deg=heading,
            ground_speed_m_s=0 if mission_state is MissionState.COMPLETED else 7.5,
            battery_percent=battery,
            battery_voltage=13.2 + battery / 100 * 3.6,
            gps_fix_type=2 if self.gps_degraded else 3,
            satellites=5 if self.gps_degraded else 14,
            flight_mode="SIM_RTL" if mission_state is MissionState.RETURNING else "SIM_SEARCH",
            armed=step not in {0, self.total_steps - 1},
            link_quality=0 if self.link_lost else 96,
            current_task="SIMULATED_RETURN" if mission_state is MissionState.RETURNING else "SIMULATED_SEARCH",
            last_update=datetime.now(UTC),
            sequence=step + 1,
            simulated=True,
        )
        events: list[MissionEvent] = []
        targets: list[Target] = []
        if step == 0:
            events.extend([
                self._event(EventType.MISSION_STARTED, "GCS simulation started"),
                self._event(EventType.VEHICLE_CONNECTED, "Simulated vehicle connected"),
                self._event(EventType.SEARCH_STARTED, "Simulated search events started"),
            ])
        candidate_step = max(2, self.total_steps // 4)
        confirm_step = max(candidate_step + 1, self.total_steps // 2)
        delivery_step = max(confirm_step + 1, (self.total_steps * 3) // 4)
        if step == candidate_step:
            enu = self.frame.to_enu(self.mission.boundary.origin.model_copy(update={"latitude": latitude, "longitude": longitude}))
            self.target = Target(
                target_id=str(uuid5(NAMESPACE_URL, f"{self.mission.id}:{self.seed}:target-1")),
                mission_id=self.mission.id,
                latitude=latitude,
                longitude=longitude,
                east_m=enu.east_m,
                north_m=enu.north_m,
                detection_confidence=round(self.random.uniform(0.72, 0.82), 3),
                geolocation_uncertainty_m=4.5,
                assigned_vehicle_id="drone_01",
                simulated=True,
            )
            targets.append(self.target)
            events.append(self._event(EventType.TARGET_CANDIDATE, "Simulated target candidate created", self.target.target_id))
        elif step == confirm_step and self.target:
            self.target.state = TargetState.CONFIRMED
            self.target.observation_count = 3
            self.target.detection_confidence = 0.93
            self.target.last_seen_at = datetime.now(UTC)
            targets.append(self.target)
            events.append(self._event(EventType.TARGET_CONFIRMED, "Simulated target confirmed", self.target.target_id))
        elif step == delivery_step and self.target:
            self.target.state = TargetState.DELIVERING
            self.target.delivery_state = DeliveryState.IN_PROGRESS
            targets.append(self.target)
            events.append(self._event(EventType.DELIVERY_STARTED, "Simulated delivery event started", self.target.target_id))
        elif step == delivery_step + 1 and self.target:
            self.target.state = TargetState.DELIVERED
            self.target.delivery_state = DeliveryState.DELIVERED
            targets.append(self.target)
            events.append(self._event(EventType.DELIVERY_COMPLETED, "Simulated delivery event completed", self.target.target_id))
        if mission_state is MissionState.RETURNING:
            events.append(self._event(EventType.RTL_STARTED, "Simulated return sequence started"))
        if mission_state is MissionState.COMPLETED:
            events.append(self._event(EventType.MISSION_COMPLETED, "GCS simulation completed"))
        return SimulatorUpdate(
            vehicle=vehicle,
            mission_state=mission_state,
            elapsed_seconds=float(step),
            coverage_percent=round(progress * 100, 2),
            events=events,
            targets=targets,
        )

    def inject_target(self) -> Target:
        longitude, latitude = self.path[min(self.step_index, self.total_steps - 1)]
        enu = self.frame.to_enu(self.mission.boundary.origin.model_copy(update={"latitude": latitude, "longitude": longitude}))
        self.target = Target(
            mission_id=self.mission.id,
            latitude=latitude,
            longitude=longitude,
            east_m=enu.east_m,
            north_m=enu.north_m,
            detection_confidence=0.75,
            geolocation_uncertainty_m=5,
            assigned_vehicle_id="drone_01",
        )
        return self.target

    def _event(self, event_type: EventType, message: str, target_id: str | None = None) -> MissionEvent:
        severity = EventSeverity.WARNING if event_type in {EventType.WARNING, EventType.FAILSAFE} else EventSeverity.INFO
        return MissionEvent(
            mission_id=self.mission.id,
            vehicle_id="drone_01",
            target_id=target_id,
            type=event_type,
            severity=severity,
            message=message,
            simulated=True,
        )
