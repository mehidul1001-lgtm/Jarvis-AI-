"""Registration, login, token refresh with rotation, and logout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.session import UserSession
from app.models.user import User, UserRole
from app.schemas.auth import TokenPair
from app.services.audit_service import AuditService


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)
        self.settings = get_settings()

    # --- Registration ----------------------------------------------------

    async def register(
        self,
        email: str,
        full_name: str,
        password: str,
        request: Request | None = None,
    ) -> User:
        email = email.lower().strip()
        existing = await self.db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("An account with this email already exists")

        # Bootstrap: the very first account becomes the administrator.
        user_count = (await self.db.execute(select(func.count()).select_from(User))).scalar_one()
        role = UserRole.ADMIN if user_count == 0 else UserRole.USER

        user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role,
        )
        self.db.add(user)
        await self.db.flush()
        await self.audit.record(
            "auth.register", user_id=user.id, resource=f"user:{user.id}", request=request
        )
        return user

    # --- Login ------------------------------------------------------------

    async def login(self, email: str, password: str, request: Request | None = None) -> TokenPair:
        email = email.lower().strip()
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        # Constant-shape flow: hash check runs even for unknown users would
        # require a dummy hash; instead we keep error messages identical so
        # the response does not reveal whether the account exists.
        if user is None or not verify_password(password, user.password_hash):
            await self.audit.record("auth.login_failed", detail={"email": email}, request=request)
            # Commit before raising: the request-scoped session rolls back on
            # exceptions, which would silently drop this audit entry.
            await self.db.commit()
            raise AuthenticationError("Invalid email or password")
        if not user.is_active:
            await self.audit.record("auth.login_blocked", user_id=user.id, request=request)
            await self.db.commit()
            raise AuthenticationError("This account has been deactivated")

        await self._enforce_session_cap(user)
        pair = await self._issue_tokens(user, request)
        user.last_login_at = datetime.now(UTC)
        await self.audit.record("auth.login", user_id=user.id, request=request)
        return pair

    # --- Refresh with rotation ---------------------------------------------

    async def refresh(self, refresh_token: str, request: Request | None = None) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        result = await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise AuthenticationError("Invalid refresh token")
        if not session.is_active:
            # Reuse of a rotated/revoked token: revoke every session for the
            # user, since the token may have been stolen.
            await self._revoke_all_sessions(session.user_id)
            await self.audit.record(
                "auth.refresh_token_reuse",
                user_id=session.user_id,
                detail={"session_id": str(session.id)},
                request=request,
            )
            # Commit before raising, otherwise the request-scoped session
            # rolls back and the mass revocation never takes effect.
            await self.db.commit()
            raise AuthenticationError("Refresh token is no longer valid")

        user = await self.db.get(User, session.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Account is not available")

        # Rotate: retire the old session record, issue a new one.
        session.revoked_at = datetime.now(UTC)
        pair = await self._issue_tokens(user, request)
        await self.audit.record("auth.refresh", user_id=user.id, request=request)
        return pair

    # --- Logout -------------------------------------------------------------

    async def logout(self, refresh_token: str, request: Request | None = None) -> None:
        token_hash = hash_refresh_token(refresh_token)
        result = await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            await self.audit.record("auth.logout", user_id=session.user_id, request=request)

    async def revoke_session(self, user: User, session_id) -> bool:
        """Revoke one of the user's own sessions (device management)."""
        session = await self.db.get(UserSession, session_id)
        if session is None or session.user_id != user.id:
            return False
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            await self.audit.record(
                "auth.session_revoked",
                user_id=user.id,
                detail={"session_id": str(session.id)},
            )
        return True

    # --- Internals ------------------------------------------------------------

    async def _issue_tokens(self, user: User, request: Request | None) -> TokenPair:
        refresh_token = generate_refresh_token()
        session = UserSession(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.refresh_token_expire_days),
            user_agent=(request.headers.get("user-agent") if request else None),
            ip_address=(request.client.host if request and request.client else None),
        )
        self.db.add(session)
        await self.db.flush()

        access_token = create_access_token(user.id, user.role.value, session.id)
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self.settings.access_token_expire_minutes * 60,
        )

    async def _enforce_session_cap(self, user: User) -> None:
        """Keep at most N active sessions per user; revoke the oldest."""
        result = await self.db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
            .order_by(UserSession.created_at.asc())
        )
        active = list(result.scalars().all())
        overflow = len(active) - self.settings.max_sessions_per_user + 1
        for session in active[: max(overflow, 0)]:
            session.revoked_at = datetime.now(UTC)

    async def _revoke_all_sessions(self, user_id) -> None:
        result = await self.db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
        now = datetime.now(UTC)
        for session in result.scalars().all():
            session.revoked_at = now
