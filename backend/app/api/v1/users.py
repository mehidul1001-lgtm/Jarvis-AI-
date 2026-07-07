"""Admin user management endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.api.deps import AdminUser, DbSession
from app.schemas.common import Page
from app.schemas.user import UserOut, UserUpdateAdmin
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
async def list_users(
    admin: AdminUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[UserOut]:
    users, total = await UserService(db).list(page=page, page_size=page_size)
    return Page(
        items=[UserOut.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession) -> UserOut:
    return UserOut.model_validate(await UserService(db).get(user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateAdmin,
    admin: AdminUser,
    db: DbSession,
    request: Request,
) -> UserOut:
    user = await UserService(db).update_as_admin(admin, user_id, payload, request)
    return UserOut.model_validate(user)
