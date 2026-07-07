"""Shared pytest fixtures: isolated test database + ASGI test client."""
from __future__ import annotations

import os

# Configure the environment BEFORE importing the application, because
# settings are cached at first import.
TEST_DATABASE_URL = os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis_dev_password@localhost:5432/jarvis_test",
)
os.environ.setdefault("JARVIS_ENVIRONMENT", "test")
os.environ.setdefault("JARVIS_RATE_LIMIT_ENABLED", "false")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from app.core.database import db
from app.main import app
from app.models import Base

SYNC_URL = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    engine = create_engine(SYNC_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(_create_schema):
    yield
    engine = create_engine(SYNC_URL)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE audit_logs, user_sessions, users CASCADE"))
    engine.dispose()


@pytest.fixture
async def client():
    db.init(TEST_DATABASE_URL)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    await db.dispose()


REGISTER_PAYLOAD = {
    "email": "admin@example.com",
    "full_name": "Admin User",
    "password": "Str0ngPassword!",
}


@pytest.fixture
async def admin_tokens(client: AsyncClient) -> dict:
    """Registers the first (admin) user and returns their token pair."""
    resp = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def user_tokens(client: AsyncClient, admin_tokens: dict) -> dict:
    """Registers a second (regular) user and returns their token pair."""
    payload = {
        "email": "user@example.com",
        "full_name": "Regular User",
        "password": "An0therPassword!",
    }
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth_header(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
