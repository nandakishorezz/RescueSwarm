"""One mission-scoped realtime channel shared by all UI features."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from .telemetry_service import RealtimeEnvelope


class WebSocketManager:
    def __init__(self, send_timeout_s: float = 0.25) -> None:
        if send_timeout_s <= 0:
            raise ValueError("WebSocket send timeout must be positive")
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.send_timeout_s = send_timeout_s

    async def connect(self, mission_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[mission_id].add(websocket)

    def disconnect(self, mission_id: str, websocket: WebSocket) -> None:
        self.connections[mission_id].discard(websocket)
        if not self.connections[mission_id]:
            self.connections.pop(mission_id, None)

    async def broadcast(self, envelope: RealtimeEnvelope) -> None:
        connections = tuple(self.connections.get(envelope.mission_id, set()))

        async def send(websocket: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(
                    websocket.send_json(envelope.model_dump(mode="json")),
                    timeout=self.send_timeout_s,
                )
                return None
            except (Exception, asyncio.TimeoutError):
                return websocket

        # A stalled browser may expire, but it cannot serialize or indefinitely
        # block fleet state delivery to other clients or mission control.
        dead = [item for item in await asyncio.gather(*(send(item) for item in connections)) if item]
        for websocket in dead:
            self.disconnect(envelope.mission_id, websocket)
