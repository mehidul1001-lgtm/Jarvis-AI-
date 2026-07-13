"""Long-term memory entries.

One table with a ``kind`` discriminator covers every category JARVIS
remembers (preferences, products, suppliers, customers, projects, code
notes, business rules, tasks, goals, decisions, learned facts, conversation
summaries, documents). Retrieval combines PostgreSQL full-text search,
trigram similarity, importance and recency into one ranked query.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Computed, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MemoryKind(str, enum.Enum):
    CONVERSATION_SUMMARY = "conversation_summary"
    PREFERENCE = "preference"
    PRODUCT = "product"
    SUPPLIER = "supplier"
    CUSTOMER = "customer"
    PROJECT = "project"
    CODE_NOTE = "code_note"
    BUSINESS_RULE = "business_rule"
    TASK_NOTE = "task_note"
    GOAL = "goal"
    DECISION = "decision"
    LEARNED = "learned"
    DOCUMENT = "document"


class MemoryEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "memory_entries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    kind: Mapped[MemoryKind] = mapped_column(
        Enum(MemoryKind, name="memory_kind", values_callable=lambda e: [m.value for m in e]),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Generated column kept in sync by PostgreSQL; GIN-indexed for FTS.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || content)", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_memory_entries_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_memory_entries_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )
