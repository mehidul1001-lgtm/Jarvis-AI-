"""Memory explorer API: CRUD, search, import/export."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models.memory import MemoryKind
from app.schemas.ai import (
    MemoryCreate,
    MemoryImport,
    MemoryOut,
    MemorySearchOut,
    MemoryUpdate,
)
from app.schemas.common import Message, Page
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(payload: MemoryCreate, user: CurrentUser, db: DbSession) -> MemoryOut:
    entry = await MemoryService(db).create(
        user,
        kind=payload.kind,
        title=payload.title,
        content=payload.content,
        importance=payload.importance,
        detail=payload.detail,
        source="manual",
    )
    return MemoryOut.model_validate(entry)


@router.get("", response_model=Page[MemoryOut])
async def list_memories(
    user: CurrentUser,
    db: DbSession,
    kind: MemoryKind | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[MemoryOut]:
    entries, total = await MemoryService(db).list(user, kind=kind, page=page, page_size=page_size)
    return Page(
        items=[MemoryOut.model_validate(e) for e in entries],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=list[MemorySearchOut])
async def search_memories(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(min_length=1, max_length=500),
    kind: MemoryKind | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[MemorySearchOut]:
    results = await MemoryService(db).search(user, q, kind=kind, limit=limit, record_access=False)
    return [
        MemorySearchOut(entry=MemoryOut.model_validate(entry), score=round(score, 4))
        for entry, score in results
    ]


@router.get("/export")
async def export_memories(user: CurrentUser, db: DbSession) -> dict:
    items = await MemoryService(db).export(user)
    return {"version": 1, "count": len(items), "items": items}


@router.post("/import", response_model=Message)
async def import_memories(payload: MemoryImport, user: CurrentUser, db: DbSession) -> Message:
    count = await MemoryService(db).import_entries(user, payload.items)
    return Message(message=f"Imported {count} memories")


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(memory_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MemoryOut:
    return MemoryOut.model_validate(await MemoryService(db).get(user, memory_id))


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: uuid.UUID, payload: MemoryUpdate, user: CurrentUser, db: DbSession
) -> MemoryOut:
    entry = await MemoryService(db).update(
        user,
        memory_id,
        kind=payload.kind,
        title=payload.title,
        content=payload.content,
        importance=payload.importance,
        detail=payload.detail,
    )
    return MemoryOut.model_validate(entry)


@router.delete("/{memory_id}", response_model=Message)
async def delete_memory(memory_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Message:
    await MemoryService(db).delete(user, memory_id)
    return Message(message="Memory deleted")
