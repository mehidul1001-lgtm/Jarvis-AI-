"""User management (self-service and admin)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.security import hash_password, verify_password
from app.models.session import UserSession
from app.models.user import User, UserRole
from app.schemas.user import UserUpdateAdmin, UserUpdateSelf
from app.services.audit_service import AuditService


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def list(self, *, page: int = 1, page_size: int = 20) -> tuple[list[User], int]:
        total = (await self.db.execute(select(func.count()).select_from(User))).scalar_one()
        result = await self.db.execute(
            select(User)
            .order_by(User.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_self(self, user: User, payload: UserUpdateSelf) -> User:
        if payload.full_name is not None:
            user.full_name = payload.full_name.strip()
        await self.db.flush()
        return user

    async def update_as_admin(
        self,
        actor: User,
        user_id: uuid.UUID,
        payload: UserUpdateAdmin,
        request: Request | None = None,
    ) -> User:
        user = await self.get(user_id)
        if user.id == actor.id and payload.role is not None and payload.role != UserRole.ADMIN:
            raise ValidationFailedError("Administrators cannot demote themselves")
        if user.id == actor.id and payload.is_active is False:
            raise ValidationFailedError("Administrators cannot deactivate themselves")

        changes: dict[str, str] = {}
        if payload.full_name is not None:
            user.full_name = payload.full_name.strip()
            changes["full_name"] = user.full_name
        if payload.role is not None and payload.role != user.role:
            user.role = payload.role
            changes["role"] = payload.role.value
        if payload.is_active is not None and payload.is_active != user.is_active:
            user.is_active = payload.is_active
            changes["is_active"] = str(payload.is_active)
            if not payload.is_active:
                await self._revoke_sessions(user.id)

        if changes:
            await self.audit.record(
                "user.updated_by_admin",
                user_id=actor.id,
                resource=f"user:{user.id}",
                detail=changes,
                request=request,
            )
        await self.db.flush()
        return user

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        request: Request | None = None,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")
        user.password_hash = hash_password(new_password)
        # Changing the password invalidates every other session.
        await self._revoke_sessions(user.id)
        await self.audit.record("user.password_changed", user_id=user.id, request=request)
        await self.db.flush()

    async def list_sessions(self, user: User) -> list[UserSession]:
        result = await self.db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
            .order_by(UserSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def _revoke_sessions(self, user_id: uuid.UUID) -> None:
        result = await self.db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
        now = datetime.now(UTC)
        for session in result.scalars().all():
            session.revoked_at = now
