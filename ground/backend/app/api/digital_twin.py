"""Read-only Stage 11S digital-twin, replay, and safe artifact surfaces."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from rescue_core.geolocation.transforms import (
    body_to_enu_rotation,
    matrix_to_quaternion,
    quaternion_from_euler,
)

from ..domain.mission import GeoPoint
from ..domain.vehicle import VehicleState
from ..repositories.database import TelemetryRecord
from ..services.coordinates import EnuPoint, LocalCoordinateFrame

router = APIRouter(prefix="/api/missions", tags=["digital-twin"])
artifact_router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _body_to_enu_quaternion(vehicle: VehicleState) -> list[float]:
    body_to_ned = vehicle.quaternion_body_to_ned or quaternion_from_euler(
        math.radians(vehicle.roll_deg), math.radians(vehicle.pitch_deg), math.radians(vehicle.yaw_deg)
    )
    return matrix_to_quaternion(body_to_enu_rotation(body_to_ned)).tolist()


def _vehicle_view(vehicle: VehicleState, frame: LocalCoordinateFrame | None, stale_seconds: float) -> dict[str, object]:
    if frame:
        point = frame.to_enu(GeoPoint(
            latitude=vehicle.latitude, longitude=vehicle.longitude,
            altitude_m=(frame.origin.altitude_m or 0.0) + vehicle.relative_altitude_m,
        ))
        position = [point.east_m, point.north_m, vehicle.relative_altitude_m]
    else:
        position = [0.0, 0.0, vehicle.relative_altitude_m]
    updated = vehicle.last_update.astimezone(UTC)
    return {
        "vehicle_id": vehicle.vehicle_id,
        "position_enu_m": position,
        "position_wgs84": {
            "latitude": vehicle.latitude, "longitude": vehicle.longitude,
            "altitude_m": vehicle.relative_altitude_m,
        },
        "quaternion_body_to_enu": _body_to_enu_quaternion(vehicle),
        "velocity_enu_m_s": (
            [vehicle.velocity_ned_m_s[1], vehicle.velocity_ned_m_s[0], -vehicle.velocity_ned_m_s[2]]
            if vehicle.velocity_ned_m_s else [0.0, vehicle.ground_speed_m_s, vehicle.vertical_speed_m_s or 0.0]
        ),
        "telemetry_timestamp": updated.isoformat(),
        "evidence_class": "SITL" if not vehicle.simulated else "SYNTHETIC",
        "attitude_source": vehicle.attitude_source if vehicle.quaternion_body_to_ned else "EULER_DERIVED_DISPLAY_QUATERNION",
        "stale": (datetime.now(UTC) - updated).total_seconds() > stale_seconds,
    }


def _camera_payload(config) -> dict[str, object]:
    camera = config.digital_twin.camera
    return {
        "calibration": {
            "calibration_id": camera.calibration_id,
            "distortion_model": "PINHOLE_BROWN",
            "image_width": 1280,
            "image_height": 800,
            "camera_matrix": [[800.0, 0.0, 640.0], [0.0, 800.0, 400.0], [0.0, 0.0, 1.0]],
            "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
            "resolution": [1280, 800],
            "sensor_mode": camera.sensor_mode,
            "quality": "SYNTHETIC_TEST_ONLY",
            "evidence_class": camera.evidence_class,
        },
        "extrinsics": {
            "extrinsics_id": camera.extrinsics_id,
            "mount_type": camera.mount_type,
            "translation_body_m": list(config.digital_twin.vehicle_geometry.camera_mount_position_m),
            "quaternion_camera_to_body": [math.sqrt(.5), 0.0, 0.0, math.sqrt(.5)],
            "quality": "SYNTHETIC_NADIR",
            "evidence_class": camera.evidence_class,
        },
    }


def _observations(events: list[object]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for event in events:
        metadata = event.metadata
        if event.type.value != "TARGET_OBSERVATION" or not metadata.get("camera_ray"):
            continue
        values.append({
            "observation_id": metadata["observation_id"], "target_id": event.target_id or "UNASSOCIATED",
            "vehicle_id": event.vehicle_id or "UNKNOWN", "frame_id": metadata["frame_id"],
            "capture_timestamp": metadata.get("capture_timestamp", event.timestamp.isoformat()),
            "chosen_pixel": metadata["chosen_pixel"], "pixel_strategy": metadata["pixel_strategy"],
            "camera_ray": metadata["camera_ray"], "body_ray": metadata.get("body_ray", metadata["camera_ray"]),
            "world_ray_enu": metadata["world_ray_enu"], "target_enu_m": metadata["target_enu_m"],
            "range_m": metadata["range_m"], "detection_confidence": metadata["detection_confidence"],
            "position_uncertainty_m": metadata["uncertainty_m"],
            "evidence_class": "SYNTHETIC" if event.simulated else "PHYSICAL_UNVERIFIED",
        })
    return values[-100:]


@router.get("/{mission_id}/digital-twin")
def digital_twin_snapshot(mission_id: str, request: Request) -> dict[str, object]:
    mission = request.app.state.mission_service.require(mission_id)
    vehicles = request.app.state.mission_service.vehicles.list_for_mission(mission_id)
    events = request.app.state.mission_service.events.list_for_mission(mission_id)
    frame = LocalCoordinateFrame(mission.boundary.origin) if mission.boundary else None
    config = request.app.state.config
    geometry = config.digital_twin.vehicle_geometry
    return {
        "mission_id": mission_id, "generated_at": datetime.now(UTC).isoformat(),
        "execution_source": "SITL" if any(not vehicle.simulated for vehicle in vehicles) else "SIMULATED",
        "sequence": max((vehicle.sequence for vehicle in vehicles), default=0),
        "origin": mission.boundary.origin.model_dump(mode="json") if mission.boundary else None,
        "ground": {
            "provider": config.digital_twin.ground.provider.upper(),
            "elevation_up_m": config.digital_twin.ground.elevation_up_m,
            "evidence_class": config.digital_twin.ground.evidence_class,
        },
        "vehicle_geometry": geometry.model_dump(mode="json"),
        "camera": _camera_payload(config),
        "vehicles": [_vehicle_view(vehicle, frame, config.realtime.degraded_threshold_seconds) for vehicle in vehicles],
        "observations": _observations(events),
        "launch_landing_zone": {
            "width_m": config.fleet.launch_zone_width_m, "length_m": config.fleet.launch_zone_length_m,
            "physical_fit_verified": False,
        },
        "hardware_gate_h1": "PENDING",
    }


@router.get("/{mission_id}/replay")
def replay_bundle(mission_id: str, request: Request) -> dict[str, object]:
    mission = request.app.state.mission_service.require(mission_id)
    frame = LocalCoordinateFrame(mission.boundary.origin) if mission.boundary else None
    events = request.app.state.mission_service.events.list_for_mission(mission_id)
    with request.app.state.database.session_factory() as session:
        records = session.scalars(
            select(TelemetryRecord).where(TelemetryRecord.mission_id == mission_id).order_by(TelemetryRecord.timestamp)
        ).all()
    samples: list[dict[str, object]] = []
    for record in records[-10_000:]:
        vehicle = VehicleState.model_validate(record.payload)
        view = _vehicle_view(vehicle, frame, 10**9)
        samples.append({
            "timestamp": record.timestamp.replace(tzinfo=UTC).isoformat() if record.timestamp.tzinfo is None else record.timestamp.isoformat(),
            "vehicle_id": record.vehicle_id, "position_wgs84": view["position_wgs84"],
            "position_enu_m": view["position_enu_m"], "quaternion_body_to_enu": view["quaternion_body_to_enu"],
            "mode": vehicle.flight_mode, "mission_state": vehicle.current_task,
            "payload_state": None, "evidence_class": view["evidence_class"],
        })
    execution = request.app.state.execution_repository.get(mission_id)
    source = execution.source.value if execution else ("SITL" if any(item["evidence_class"] == "SITL" for item in samples) else "SIMULATED")
    return {
        "mission_id": mission_id,
        "start_time": samples[0]["timestamp"] if samples else (mission.started_at.isoformat() if mission.started_at else None),
        "end_time": samples[-1]["timestamp"] if samples else (mission.completed_at.isoformat() if mission.completed_at else None),
        "samples": samples,
        "events": [event.model_dump(mode="json") for event in events],
        "observations": _observations(events), "deterministic": True, "source": source,
    }


@router.get("/{mission_id}/artifacts")
def mission_artifacts(mission_id: str, request: Request) -> dict[str, object]:
    request.app.state.mission_service.require(mission_id)
    allowed = ["STAGE_10S_REPORT.md", "COMPETITION_READINESS_REPORT.md", "STAGE_11S_REPORT.md"]
    return {
        "mission_id": mission_id,
        "artifacts": [{"name": name, "url": f"/api/artifacts/{name}"} for name in allowed if (REPOSITORY_ROOT / name).is_file()],
    }


@artifact_router.get("/{name:path}")
def get_artifact(name: str) -> FileResponse:
    allowed = {"STAGE_10S_REPORT.md", "COMPETITION_READINESS_REPORT.md", "STAGE_11S_REPORT.md"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="Artifact is not exposed")
    path = REPOSITORY_ROOT / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact is not available")
    return FileResponse(path, media_type="text/markdown", filename=name)
