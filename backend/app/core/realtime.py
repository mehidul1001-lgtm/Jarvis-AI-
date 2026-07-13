"""Realtime WebSocket connection registry.

Lives in ``core`` (not the API layer) so services — the brain streaming chat
tokens, the workflow engine publishing task progress — can push events to
connected clients without importing from ``app.api``.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket

logger = logging.getLogger("jarvis.realtime")


class ConnectionManager:
    """Tracks authenticated WebSocket connections per user."""

    def __init__(self) -> None:
        self.connections: dict[uuid.UUID, list[WebSocket]] = {}

    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        sockets = self.connections.get(user_id, [])
        if websocket in sockets:
            sockets.remove(websocket)
        if not sockets:
            self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: uuid.UUID, payload: dict) -> None:
        for websocket in list(self.connections.get(user_id, [])):
            try:
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(user_id, websocket)


manager = ConnectionManager()
