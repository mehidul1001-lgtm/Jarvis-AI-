"""Long-term memory: CRUD, ranked semantic search, aging, import/export.

Search strategy (no external embedding dependency):
  rank = 0.5 * text_relevance + 0.3 * importance + 0.2 * recency
where text_relevance combines PostgreSQL full-text `ts_rank` with trigram
similarity on the title (catches misspellings and partial matches). Memory
importance decays with age unless entries are re-accessed ("aging").
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.memory import MemoryEntry, MemoryKind
from app.models.user import User
from app.services.audit_service import AuditService

logger = logging.getLogger("jarvis.memory")

# Half-life for the recency component of ranking.
RECENCY_HALF_LIFE_DAYS = 30.0
# Aging: importance decays by this factor per period for untouched entries.
AGING_DECAY = 0.9
AGING_PERIOD_DAYS = 30
AGING_FLOOR = 0.05


class MemoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    # --- CRUD ---------------------------------------------------------------

    async def create(
        self,
        user: User,
        *,
        kind: MemoryKind,
        title: str,
        content: str,
        importance: float = 0.5,
        source: str | None = None,
        detail: dict | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            user_id=user.id,
            kind=kind,
            title=title.strip()[:300],
            content=content.strip(),
            importance=max(0.0, min(1.0, importance)),
            source=source,
            detail=detail,
        )
        self.db.add(entry)
        await self.db.flush()
        await self.audit.record(
            "memory.created",
            user_id=user.id,
            resource=f"memory:{entry.id}",
            detail={"kind": kind.value, "title": entry.title, "source": source},
        )
        return entry

    async def get(self, user: User, memory_id: uuid.UUID) -> MemoryEntry:
        entry = await self.db.get(MemoryEntry, memory_id)
        if entry is None or entry.user_id != user.id:
            # Same error whether missing or foreign: no existence leak.
            raise NotFoundError("Memory not found")
        return entry

    async def update(
        self,
        user: User,
        memory_id: uuid.UUID,
        *,
        title: str | None = None,
        content: str | None = None,
        importance: float | None = None,
        kind: MemoryKind | None = None,
        detail: dict | None = None,
    ) -> MemoryEntry:
        entry = await self.get(user, memory_id)
        changes: dict[str, Any] = {}
        if title is not None:
            entry.title = title.strip()[:300]
            changes["title"] = entry.title
        if content is not None:
            entry.content = content.strip()
            changes["content"] = True
        if importance is not None:
            entry.importance = max(0.0, min(1.0, importance))
            changes["importance"] = entry.importance
        if kind is not None:
            entry.kind = kind
            changes["kind"] = kind.value
        if detail is not None:
            entry.detail = detail
            changes["detail"] = True
        await self.db.flush()
        if changes:
            await self.audit.record(
                "memory.updated",
                user_id=user.id,
                resource=f"memory:{entry.id}",
                detail=changes,
            )
        return entry

    async def delete(self, user: User, memory_id: uuid.UUID) -> None:
        entry = await self.get(user, memory_id)
        await self.db.delete(entry)
        await self.db.flush()
        await self.audit.record("memory.deleted", user_id=user.id, resource=f"memory:{memory_id}")

    async def list(
        self,
        user: User,
        *,
        kind: MemoryKind | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MemoryEntry], int]:
        query = select(MemoryEntry).where(MemoryEntry.user_id == user.id)
        if kind is not None:
            query = query.where(MemoryEntry.kind == kind)
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(MemoryEntry.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    # --- Search ---------------------------------------------------------------

    async def search(
        self,
        user: User,
        query: str,
        *,
        kind: MemoryKind | None = None,
        limit: int = 8,
        record_access: bool = True,
    ) -> list[tuple[MemoryEntry, float]]:
        """Ranked hybrid search over the user's memories."""
        query = query.strip()
        if not query:
            return []

        ts_query = func.plainto_tsquery("english", query)
        ts_rank = func.ts_rank(MemoryEntry.search_vector, ts_query)
        title_sim = func.similarity(MemoryEntry.title, query)
        text_relevance = func.greatest(ts_rank, title_sim)
        age_days = cast(func.extract("epoch", func.now() - MemoryEntry.updated_at) / 86400.0, Float)
        recency = func.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
        score = (0.5 * text_relevance + 0.3 * MemoryEntry.importance + 0.2 * recency).label("score")

        stmt = (
            select(MemoryEntry, score)
            .where(
                MemoryEntry.user_id == user.id,
                or_(
                    MemoryEntry.search_vector.op("@@")(ts_query),
                    title_sim > 0.15,
                ),
            )
            .order_by(score.desc())
            .limit(limit)
        )
        if kind is not None:
            stmt = stmt.where(MemoryEntry.kind == kind)

        rows = (await self.db.execute(stmt)).all()
        entries = [(row[0], float(row[1])) for row in rows]

        if record_access and entries:
            now = datetime.now(UTC)
            for entry, _ in entries:
                entry.access_count += 1
                entry.last_accessed_at = now
            await self.db.flush()
        return entries

    # --- Aging -------------------------------------------------------------

    async def age_memories(self, user: User | None = None) -> int:
        """Decay importance of stale, rarely accessed memories.

        Run periodically by the operations agent / workflow engine. Returns
        the number of entries decayed.
        """
        cutoff = datetime.now(UTC) - timedelta(days=AGING_PERIOD_DAYS)
        stmt = select(MemoryEntry).where(
            MemoryEntry.updated_at < cutoff,
            or_(
                MemoryEntry.last_accessed_at.is_(None),
                MemoryEntry.last_accessed_at < cutoff,
            ),
            MemoryEntry.importance > AGING_FLOOR,
        )
        if user is not None:
            stmt = stmt.where(MemoryEntry.user_id == user.id)
        entries = list((await self.db.execute(stmt)).scalars().all())
        for entry in entries:
            entry.importance = max(AGING_FLOOR, entry.importance * AGING_DECAY)
        await self.db.flush()
        return len(entries)

    # --- Import / export ------------------------------------------------------

    async def export(self, user: User) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(MemoryEntry)
            .where(MemoryEntry.user_id == user.id)
            .order_by(MemoryEntry.created_at.asc())
        )
        return [
            {
                "kind": entry.kind.value,
                "title": entry.title,
                "content": entry.content,
                "importance": entry.importance,
                "source": entry.source,
                "detail": entry.detail,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in result.scalars().all()
        ]

    async def import_entries(self, user: User, items: list[dict[str, Any]]) -> int:
        if len(items) > 5000:
            raise ValidationFailedError("Import is limited to 5000 entries per request")
        imported = 0
        for item in items:
            try:
                kind = MemoryKind(item["kind"])
                title = str(item["title"]).strip()
                content = str(item["content"]).strip()
            except (KeyError, ValueError) as exc:
                raise ValidationFailedError(
                    f"Invalid memory entry at index {imported}: {exc}"
                ) from exc
            if not title or not content:
                raise ValidationFailedError(
                    f"Memory entry at index {imported} is missing title or content"
                )
            self.db.add(
                MemoryEntry(
                    user_id=user.id,
                    kind=kind,
                    title=title[:300],
                    content=content,
                    importance=max(0.0, min(1.0, float(item.get("importance", 0.5)))),
                    source=item.get("source") or "import",
                    detail=item.get("detail"),
                )
            )
            imported += 1
        await self.db.flush()
        await self.audit.record("memory.imported", user_id=user.id, detail={"count": imported})
        return imported
