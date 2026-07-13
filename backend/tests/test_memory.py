"""Memory system tests: ranking, isolation, aging, import/export, API."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.core.database import db
from app.models.memory import MemoryKind
from app.models.user import User
from app.services.memory_service import MemoryService
from tests.conftest import auth_header


async def _user_of(tokens_email: str) -> User:
    from sqlalchemy import select

    async with db.sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == tokens_email))
        return result.scalar_one()


async def test_search_ranks_relevance_and_importance(client: AsyncClient, admin_tokens):
    user = await _user_of("admin@example.com")
    async with db.sessionmaker() as session:
        service = MemoryService(session)
        await service.create(
            user,
            kind=MemoryKind.SUPPLIER,
            title="Shenzhen widget supplier",
            content="Acme Manufacturing in Shenzhen supplies widgets at $2.10/unit MOQ 500.",
            importance=0.9,
        )
        await service.create(
            user,
            kind=MemoryKind.PREFERENCE,
            title="Coffee preference",
            content="User prefers oat milk lattes.",
            importance=0.2,
        )
        await session.commit()

        results = await service.search(user, "widget supplier in Shenzhen")
        assert results, "expected at least one hit"
        top, score = results[0]
        assert top.title == "Shenzhen widget supplier"
        assert score > 0
        # Access recorded for retrieved entries.
        assert top.access_count == 1


async def test_memory_is_isolated_per_user(client: AsyncClient, admin_tokens, user_tokens):
    admin = await _user_of("admin@example.com")
    regular = await _user_of("user@example.com")
    async with db.sessionmaker() as session:
        service = MemoryService(session)
        entry = await service.create(
            admin,
            kind=MemoryKind.BUSINESS_RULE,
            title="Margin floor",
            content="Never launch products under 25% net margin.",
            importance=1.0,
        )
        await session.commit()

        # Search as the other user finds nothing.
        assert await service.search(regular, "margin floor") == []

    # API access to a foreign memory id is a 404, not a 403 (no existence leak).
    resp = await client.get(f"/api/v1/memory/{entry.id}", headers=auth_header(user_tokens))
    assert resp.status_code == 404
    resp = await client.patch(
        f"/api/v1/memory/{entry.id}",
        headers=auth_header(user_tokens),
        json={"title": "hijacked"},
    )
    assert resp.status_code == 404


async def test_aging_decays_stale_memories(client: AsyncClient, admin_tokens):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.models.memory import MemoryEntry

    user = await _user_of("admin@example.com")
    async with db.sessionmaker() as session:
        service = MemoryService(session)
        entry = await service.create(
            user,
            kind=MemoryKind.LEARNED,
            title="Old fact",
            content="Something from long ago.",
            importance=0.8,
        )
        old = datetime.now(UTC) - timedelta(days=90)
        await session.execute(
            update(MemoryEntry).where(MemoryEntry.id == entry.id).values(updated_at=old)
        )
        aged = await service.age_memories(user)
        await session.commit()
        assert aged == 1
        refreshed = await session.get(MemoryEntry, entry.id)
        assert refreshed.importance < 0.8


async def test_export_import_roundtrip_api(client: AsyncClient, admin_tokens):
    headers = auth_header(admin_tokens)
    resp = await client.post(
        "/api/v1/memory",
        headers=headers,
        json={
            "kind": "product",
            "title": "Bamboo cutting board",
            "content": "20x14in, landed cost $4.80, target price $24.99.",
            "importance": 0.7,
        },
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/memory/export", headers=headers)
    assert resp.status_code == 200
    exported = resp.json()
    assert exported["count"] == 1

    resp = await client.post(
        "/api/v1/memory/import", headers=headers, json={"items": exported["items"]}
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/memory", headers=headers)
    assert resp.json()["total"] == 2

    resp = await client.get("/api/v1/memory/search", headers=headers, params={"q": "bamboo board"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_import_rejects_bad_entries(client: AsyncClient, admin_tokens):
    resp = await client.post(
        "/api/v1/memory/import",
        headers=auth_header(admin_tokens),
        json={"items": [{"kind": "nonsense", "title": "x", "content": "y"}]},
    )
    assert resp.status_code == 422


async def test_get_random_memory_id_is_404(client: AsyncClient, admin_tokens):
    resp = await client.get(f"/api/v1/memory/{uuid.uuid4()}", headers=auth_header(admin_tokens))
    assert resp.status_code == 404
