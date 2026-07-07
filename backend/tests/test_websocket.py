"""WebSocket channel tests (runs the app through its full lifespan)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from tests.conftest import REGISTER_PAYLOAD


def test_websocket_requires_valid_token():
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with tc.websocket_connect("/api/v1/ws?token=invalid"):
                pass
        assert excinfo.value.code == 1008


def test_websocket_ping_pong_with_auth():
    with TestClient(app) as tc:
        resp = tc.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert resp.status_code == 201, resp.text
        resp = tc.post(
            "/api/v1/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )
        assert resp.status_code == 200, resp.text
        access_token = resp.json()["access_token"]

        with tc.websocket_connect(f"/api/v1/ws?token={access_token}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "connected"

            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

            ws.send_json({"type": "something-else"})
            assert ws.receive_json() == {"type": "ack", "received": "something-else"}
