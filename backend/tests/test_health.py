"""Health endpoint and platform hardening checks."""
from __future__ import annotations

from httpx import AsyncClient


async def test_health_reports_ok(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "test"


async def test_security_headers_present(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-ID" in resp.headers


async def test_unhandled_routes_return_404_envelope(client: AsyncClient):
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
