"""Persist and broadcast normalized Stage 5 targets without any flight-control path."""

from __future__ import annotations

from ground.backend.app.domain.events import EventType, MissionEvent
from ground.backend.app.domain.target import DeliveryState, Target, TargetState
from ground.backend.app.services.websocket_manager import RealtimeEnvelope, WebSocketManager
from rescue_core.geolocation.models import FusedTargetEstimate, TargetObservation, TargetTrackState
from rescue_core.geolocation.models import GeolocationHealth


class GeolocationMissionService:
    def __init__(self, targets, events, websocket: WebSocketManager, on_target_confirmed=None):
        self.targets = targets
        self.events = events
        self.websocket = websocket
        self.sequence = 0
        self.on_target_confirmed = on_target_confirmed

    async def publish_observation(self, mission_id: str, observation: TargetObservation) -> None:
        self.sequence += 1
        event = MissionEvent(
            mission_id=mission_id, vehicle_id=observation.vehicle_id,
            target_id=observation.target_track_id, type=EventType.TARGET_OBSERVATION,
            message=f"Target observation from frame {observation.frame_id}",
            metadata={
                "observation_id": observation.observation_id, "frame_id": observation.frame_id,
                "uncertainty_m": observation.position_uncertainty_m,
                "calibration_id": observation.camera_calibration_id,
                "extrinsics_id": observation.extrinsics_id,
                "capture_timestamp": observation.created_at.isoformat(),
                "bbox_original_px": list(observation.bbox_original_px),
                "chosen_pixel": list(observation.chosen_pixel),
                "pixel_strategy": observation.pixel_strategy.value,
                "camera_ray": list(observation.camera_ray),
                "body_ray": list(observation.camera_ray),
                "world_ray_enu": list(observation.world_ray_enu),
                "target_enu_m": [observation.target_east_m, observation.target_north_m, observation.target_up_m],
                "range_m": observation.range_m,
                "detection_confidence": observation.detection_confidence,
            },
        )
        self.events.add(event)
        await self.websocket.broadcast(RealtimeEnvelope(
            type="target.observation", mission_id=mission_id,
            vehicle_id=observation.vehicle_id, sequence=self.sequence,
            payload={
                "observation_id": observation.observation_id, "target_id": observation.target_track_id,
                "frame_id": observation.frame_id, "bbox_original_px": observation.bbox_original_px,
                "chosen_pixel": observation.chosen_pixel,
                "pixel_strategy": observation.pixel_strategy, "east_m": observation.target_east_m,
                "north_m": observation.target_north_m,
                "position_uncertainty_m": observation.position_uncertainty_m,
                "capture_timestamp": observation.created_at.isoformat(),
                "camera_ray": observation.camera_ray, "world_ray_enu": observation.world_ray_enu,
                "camera_origin_enu_m": observation.camera_origin_enu_m,
                "target_enu_m": [observation.target_east_m, observation.target_north_m, observation.target_up_m],
                "range_m": observation.range_m, "detection_confidence": observation.detection_confidence,
                "pose_age_ms": observation.interpolated_pose.diagnostics.nearest_sample_age_ms,
                "clock_sync_quality": observation.interpolated_pose.diagnostics.clock_sync_quality,
            },
        ))

    async def publish_target(self, estimate: FusedTargetEstimate) -> Target:
        existing = self.targets.get(estimate.target_id)
        # A superseded track ID is terminal for this mission.  A delayed
        # confirmation callback must not recreate it after reconciliation.
        if existing is not None and existing.state is TargetState.REJECTED:
            return existing
        state = TargetState.CONFIRMED if estimate.state is TargetTrackState.CONFIRMED else TargetState.CANDIDATE
        if existing is not None and existing.state is TargetState.DELIVERED:
            state = TargetState.DELIVERED
        delivery_state = existing.delivery_state if existing is not None and existing.delivery_state != DeliveryState.PENDING else DeliveryState.PENDING
        deliv_east = existing.delivery_point_east_m if existing is not None else None
        deliv_north = existing.delivery_point_north_m if existing is not None else None
        target = Target(
            target_id=estimate.target_id, mission_id=estimate.mission_id, state=state,
            latitude=estimate.latitude, longitude=estimate.longitude,
            east_m=estimate.east_m, north_m=estimate.north_m,
            detection_confidence=estimate.detection_confidence,
            geolocation_uncertainty_m=estimate.uncertainty_m,
            observation_count=estimate.observation_count,
            first_seen_at=estimate.first_seen_at, last_seen_at=estimate.last_seen_at,
            delivery_state=delivery_state,
            delivery_point_east_m=deliv_east,
            delivery_point_north_m=deliv_north,
            assigned_vehicle_id=estimate.assigned_vehicle_id,
            evidence_thumbnail=estimate.evidence_references[0] if estimate.evidence_references else None,
            simulated=estimate.simulated, geolocation_quality=estimate.quality,
            inlier_count=estimate.inlier_count, outlier_count=estimate.outlier_count,
            evidence_references=estimate.evidence_references,
        )
        self.targets.upsert(target)
        self.sequence += 1
        event_type = EventType.TARGET_CONFIRMED if state is TargetState.CONFIRMED else EventType.TARGET_CANDIDATE
        self.events.add(MissionEvent(
            mission_id=estimate.mission_id, vehicle_id=estimate.assigned_vehicle_id,
            target_id=estimate.target_id, type=event_type,
            message=f"Target {estimate.target_id} {state.value.lower()} with {estimate.inlier_count} inlier observations",
            metadata={
                "uncertainty_m": estimate.uncertainty_m,
                "inlier_observation_ids": estimate.inlier_observation_ids,
                "outlier_observation_ids": estimate.outlier_observation_ids,
                "calibration_ids": estimate.camera_calibration_ids,
                "extrinsics_ids": estimate.extrinsics_ids,
                "target": target.model_dump(mode="json"),
            },
        ))
        await self.websocket.broadcast(RealtimeEnvelope(
            type="target.confirmed" if state is TargetState.CONFIRMED else "target.candidate",
            mission_id=estimate.mission_id, vehicle_id=estimate.assigned_vehicle_id,
            sequence=self.sequence, payload=target.model_dump(mode="json"),
        ))
        if state is TargetState.CONFIRMED and self.on_target_confirmed is not None:
            try:
                self.on_target_confirmed(estimate)
            except Exception as exc:
                print(f"[GEOLOCATION_SERVICE] on_target_confirmed error: {exc}", flush=True)
        return target

    async def publish_target_superseded(self, superseded_target_id: str, canonical_target_id: str) -> None:
        target = self.targets.get(superseded_target_id)
        if target is not None:
            target.state = TargetState.REJECTED
            self.targets.upsert(target)
            self.sequence += 1
            await self.websocket.broadcast(RealtimeEnvelope(
                type="target.rejected",
                mission_id=target.mission_id,
                vehicle_id=target.assigned_vehicle_id,
                sequence=self.sequence,
                payload=target.model_dump(mode="json"),
            ))

    async def publish_health(
        self, mission_id: str, health: GeolocationHealth,
        clock_sync_diagnostics: dict[str, object],
    ) -> None:
        self.sequence += 1
        compact_health = health.model_dump(mode="json")
        await self.websocket.broadcast(RealtimeEnvelope(
            type="geolocation.health", mission_id=mission_id, vehicle_id=None,
            sequence=self.sequence, payload=compact_health,
        ))
        await self.websocket.broadcast(RealtimeEnvelope(
            type="clock_sync.health", mission_id=mission_id, vehicle_id=None,
            sequence=self.sequence, payload={
                key: clock_sync_diagnostics.get(key)
                for key in ("sync_quality", "sample_count", "offset_ns", "rtt_median_ms", "rtt_p95_ms")
            },
        ))
