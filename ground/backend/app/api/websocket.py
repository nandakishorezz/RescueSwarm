"""Mission-scoped realtime WebSocket endpoint."""

from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.telemetry_service import RealtimeEnvelope

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/mission/{mission_id}")
async def mission_websocket(websocket: WebSocket, mission_id: str) -> None:
    service = websocket.app.state.mission_service
    fleet = websocket.app.state.multidrone_runtime
    if fleet.session is not None and str(fleet.session.session_id) == mission_id:
        # Reuse the existing mission-scoped WebSocket manager. A fleet session
        # is not a database Mission, so validate its active ID explicitly and
        # never expose the simulation's hidden scenario ground truth here.
        manager = websocket.app.state.websocket_manager
        await manager.connect(mission_id, websocket)
        await websocket.send_json(RealtimeEnvelope(
            type="system.snapshot", mission_id=mission_id, vehicle_id=None, sequence=0,
            payload={"status": "FLEET_SESSION", "fleet": fleet.status(), "snapshot": True},
        ).model_dump(mode="json"))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(mission_id, websocket)
        return
    if service.missions.get(mission_id) is None:
        await websocket.close(code=4404, reason="Mission not found")
        return
    manager = websocket.app.state.websocket_manager
    await manager.connect(mission_id, websocket)
    mission = service.missions.get(mission_id)
    vehicles = service.vehicles.list_for_mission(mission_id)
    targets = service.targets.list_for_mission(mission_id)
    resilience = websocket.app.state.resilience_manager.status(mission_id)
    await websocket.send_json(
        RealtimeEnvelope(
            type="system.snapshot",
            mission_id=mission_id,
            vehicle_id=None,
            sequence=0,
            payload={
                "status": "READY",
                "mission": mission.model_dump(mode="json") if mission else None,
                "vehicles": [item.model_dump(mode="json") for item in vehicles],
                "targets": [item.model_dump(mode="json") for item in targets],
                "resilience": resilience,
                "snapshot": True,
            },
        ).model_dump(mode="json")
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(mission_id, websocket)
