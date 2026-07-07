"""Audit log inspection (admin only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    admin: AdminUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None, max_length=100),
    user_id: uuid.UUID | None = Query(default=None),
) -> Page[AuditLogOut]:
    logs, total = await AuditService(db).list(
        page=page, page_size=page_size, action=action, user_id=user_id
    )
    return Page(
        items=[AuditLogOut.model_validate(entry) for entry in logs],
        total=total,
        page=page,
        page_size=page_size,
    )
