"""System endpoints: health check and the realtime WebSocket channel."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.database import db
from app.core.exceptions import AuthenticationError
from app.core.realtime import manager
from app.core.security import decode_access_token
from app.models.session import UserSession
from app.models.user import User
from app.schemas.common import HealthStatus

logger = logging.getLogger("jarvis.system")

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    settings = get_settings()
    database_status = "ok"
    try:
        async with db.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        database_status = "unavailable"
    return HealthStatus(
        status="ok" if database_status == "ok" else "degraded",
        version=__version__,
        environment=settings.environment,
        database=database_status,
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = "") -> None:
    """Authenticated realtime channel.

    Browsers cannot set headers on WebSocket connects, so the access token is
    passed as a query parameter: ``/api/v1/ws?token=<access_token>``.
    """
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (AuthenticationError, KeyError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with db.sessionmaker() as session:
        user_session = await session.get(UserSession, session_id)
        user = await session.get(User, user_id)
        if user_session is None or not user_session.is_active or user is None or not user.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await manager.connect(user_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "user_id": str(user_id)})
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json(
                    {"type": "ack", "received": message.get("type", "unknown")}
                )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
