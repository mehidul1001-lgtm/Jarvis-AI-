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
# Deterministic AI behavior in tests: no reflection pass unless a test
# explicitly opts in on its Brain instance; fast workflow scheduler.
os.environ.setdefault("JARVIS_AI_REFLECTION_ENABLED", "false")
os.environ.setdefault("JARVIS_WORKFLOW_POLL_INTERVAL_SECONDS", "0.05")

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
        conn.execute(
            text(
                "TRUNCATE TABLE audit_logs, user_sessions, users, conversations, "
                "messages, memory_entries, workflow_tasks, amazon_credentials, "
                "amazon_sync_states, amazon_orders, amazon_order_items, "
                "amazon_inventory_snapshots, amazon_fba_shipments, "
                "amazon_financial_events CASCADE"
            )
        )
    engine.dispose()


@pytest.fixture
def fake_llm():
    """Install a scriptable LLM client; restored after the test."""
    from app.ai.llm import set_llm_client
    from tests.fake_llm import FakeLLMClient

    client = FakeLLMClient()
    set_llm_client(client)
    yield client
    set_llm_client(None)


@pytest.fixture(scope="session", autouse=True)
def _register_workflow_handlers(_create_schema):
    """API tests need the standard handlers even without the app lifespan."""
    from app.agents.registry import register_workflow_handlers
    from app.integrations.amazon.workflow import register_amazon_workflow_handlers
    from app.workflows.engine import get_workflow_engine

    engine = get_workflow_engine()
    register_workflow_handlers(engine)
    register_amazon_workflow_handlers(engine)


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
