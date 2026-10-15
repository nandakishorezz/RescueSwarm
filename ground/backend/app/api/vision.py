"""Read-only Engineering Mode visibility for a future local Pi 5 vision link."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/engineering/vision", tags=["engineering-vision"])
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


@router.get("/status")
def vision_status(request: Request) -> dict[str, object]:
    if request.app.state.config.competition.enabled:
        raise HTTPException(status_code=403, detail="Engineering vision status is disabled in competition mode")
    path = REPOSITORY_ROOT / "data" / "vision_status.json"
    if not path.is_file():
        return {
            "connected": False,
            "source": "NOT_CONNECTED",
            "camera_health": {"state": "INITIALIZING", "reason": "No local vision-agent status received"},
            "detector_health": {"state": "INITIALIZING", "reason": "No local vision-agent status received"},
            "capture_fps": None,
            "detector_fps": None,
            "p95_latency_ms": None,
            "temperature_c": None,
            "dropped_frames": None,
            "physical_camera_verified": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Vision status artifact is unreadable: {exc}") from exc
    age_s = (time.time_ns() - int(payload.get("updated_wall_ns", 0))) / 1e9
    payload["status_age_s"] = max(0, age_s)
    if age_s > 5:
        payload["connected"] = False
        payload["link_state"] = "STALE"
    else:
        payload["link_state"] = "CONNECTED"
    return payload
