"""Shared FastAPI dependencies: current user resolution and RBAC guards."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token
from app.models.session import UserSession
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing authentication credentials")

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid authentication token") from exc

    # The access token is only honoured while its originating session lives,
    # so revoking a session (logout, password change) cuts access quickly.
    session = await db.get(UserSession, session_id)
    if session is None or not session.is_active:
        raise AuthenticationError("Session is no longer active")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not available")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole):
    async def guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise AuthorizationError()
        return user

    return guard


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
ManagerUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER))]
