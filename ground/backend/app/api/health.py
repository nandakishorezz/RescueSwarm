"""Health endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    database_ok, reason = request.app.state.database.health()
    return {
        "status": "ok" if database_ok else "degraded",
        "service": "rescueswarm-ground",
        "stage": 6,
        "timestamp": datetime.now(UTC),
        "database": {"ready": database_ok, "detail": reason},
        "real_vehicle_connected": False,
        "vehicle_sources": ["SIMULATED", "SITL"],
    }
