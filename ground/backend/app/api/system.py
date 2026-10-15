"""System/readiness and offline-compliance endpoints."""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request

from rescue_core.common.hardware_gates import H1_PHYSICAL_PROTOTYPE

router = APIRouter(prefix="/api/system", tags=["system"])
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


@router.get("/status")
def system_status(request: Request) -> dict[str, object]:
    config = request.app.state.config
    database_ok, _ = request.app.state.database.health()
    return {
        "application_mode": config.application.mode,
        "competition_mode": config.competition.enabled,
        "max_area_hectares": config.competition.max_area_hectares,
        "ground_server": "READY",
        "database": "READY" if database_ok else "UNAVAILABLE",
        "drone": "SIMULATED / NOT CONNECTED",
        "camera": "NOT VERIFIED",
        "gps": "NOT VERIFIED",
        "simulator_enabled": config.simulator.enabled and not config.competition.enabled,
        "sitl_enabled": config.sitl.enabled and not config.competition.enabled,
        "hardware_gate_h1": H1_PHYSICAL_PROTOTYPE.model_dump(mode="json"),
        "stage_7s": "SITL_ONLY",
        "stage_8s": "SITL_ONLY",
        "stage_9s": "MULTI_ARDUCOPTER_SITL_ONLY",
        "stage_10s": "SOFTWARE_RELEASE_CANDIDATE_PASS",
        "stage_11s": "OFFLINE_GCS_DIGITAL_TWIN",
        "stage_12u": "FINAL_UI_RELEASE_CANDIDATE",
        "fleet": {
            "enabled": config.fleet.enabled and not config.competition.enabled,
            "configured_size": config.fleet.fleet_size,
            "one_gcs": True,
            "physical_multi_drone": "PENDING",
        },
        "coverage_defaults": {
            "search_altitude_m": config.coverage.search_altitude_m,
            "lane_spacing_m": config.coverage.lane_spacing_m,
            "effective_swath_m": config.coverage.effective_swath_m,
            "edge_margin_m": config.coverage.edge_margin_m,
            "search_speed_m_s": config.coverage.search_speed_m_s,
            "sweep_strategy": config.coverage.sweep_strategy.upper(),
            "fixed_sweep_angle_deg": config.coverage.fixed_sweep_angle_deg,
            "turn_penalty_m": config.coverage.turn_penalty_m,
            "min_planned_coverage_percent": config.coverage.min_planned_coverage_percent,
            "max_waypoints": config.coverage.max_waypoints,
        },
        "stale_thresholds": {
            "connected_seconds": config.realtime.connected_threshold_seconds,
            "degraded_seconds": config.realtime.degraded_threshold_seconds,
        },
        "browser_limits": {
            "trail_max_points": config.realtime.trail_max_points,
            "recent_events_max": config.realtime.recent_events_max,
        },
    }


@router.get("/offline-status")
def offline_status(request: Request) -> dict[str, object]:
    database_ok, _ = request.app.state.database.health()
    return request.app.state.offline_guard.status(database_health=database_ok)


@router.get("/geolocation-status")
def geolocation_status() -> dict[str, object]:
    """Read-only local status written by a future airborne geolocation process."""
    path = REPOSITORY_ROOT / "data" / "geolocation_status.json"
    if not path.is_file():
        return {
            "connected": False, "state": "CALIBRATION_MISSING", "ready": False,
            "reason": "No local geolocation status received",
            "calibration_health": "MISSING", "clock_sync_health": "UNSYNCED",
            "pose_sync_health": "POSE_STALE", "geolocation_health": "CALIBRATION_MISSING",
            "physical_accuracy": "NOT_MEASURED", "updated_at": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))
        stale = (datetime.now(UTC) - updated.astimezone(UTC)).total_seconds() > 5
        return {**payload, "connected": not stale, "stale": stale}
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "connected": False, "state": "FAILED", "ready": False,
            "reason": f"Invalid local geolocation status: {type(exc).__name__}",
            "physical_accuracy": "NOT_MEASURED", "updated_at": None,
        }


@router.get("/hardware-validation")
def hardware_validation() -> dict[str, object]:
    """Return the newest persisted Stage 6 bench evidence; never infer hardware success."""
    root = REPOSITORY_ROOT / "data" / "hardware_runs"
    runs = sorted((path for path in root.iterdir() if path.is_dir()), reverse=True) if root.is_dir() else []
    if not runs:
        return {
            "connected": False, "status": "NOT_TESTED", "reason": "No Stage 6 physical hardware run exists",
            "source": "PHYSICAL BENCH", "physical_flight": False,
            "hardware_gate_h1": H1_PHYSICAL_PROTOTYPE.model_dump(mode="json"),
        }
    run = runs[0]
    def read(name: str) -> dict[str, object]:
        try:
            return json.loads((run / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "NOT_TESTED"}
    system = read("system.json")
    power = read("power.json")
    camera = read("camera_probe.json")
    mavlink = read("mavlink_probe.json")
    gps = read("gps.json")
    timesync = read("timesync.json")
    frame_pose = read("frame_pose_sync_metrics.json")
    geo = read("physical_geolocation.json")
    return {
        "connected": bool(system.get("platform", {}).get("raspberry_pi", False)),
        "status": "EVIDENCE_AVAILABLE", "run_id": run.name, "source": "PHYSICAL BENCH",
        "pi": "VERIFIED" if system.get("platform", {}).get("raspberry_pi") else "NOT_TESTED",
        "pixhawk": mavlink.get("status", "NOT_TESTED"), "gps": gps.get("status", "NOT_TESTED"),
        "camera": camera.get("status", "NOT_TESTED"), "power": power.get("status", "UNKNOWN"),
        "timesync": timesync.get("sync_quality", "NOT_TESTED"),
        "frame_pose_sync": frame_pose.get("status", "NOT_TESTED"),
        "calibration_id": geo.get("calibration_id", "NOT_TESTED"),
        "geolocation_test_a": geo.get("test_a", "NOT_TESTED"),
        "geolocation_test_b": geo.get("test_b", "NOT_TESTED"),
        "geolocation_test_c": geo.get("test_c", "NOT_TESTED"),
        "physical_flight": False,
        "hardware_gate_h1": H1_PHYSICAL_PROTOTYPE.model_dump(mode="json"),
    }
