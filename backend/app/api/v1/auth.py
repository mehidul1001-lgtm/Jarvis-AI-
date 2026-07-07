"""Authentication endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.common import Message
from app.schemas.user import (
    PasswordChangeRequest,
    SessionOut,
    UserOut,
    UserUpdateSelf,
)
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: DbSession, request: Request) -> UserOut:
    user = await AuthService(db).register(
        payload.email, payload.full_name, payload.password, request
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: DbSession, request: Request) -> TokenPair:
    return await AuthService(db).login(payload.email, payload.password, request)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: DbSession, request: Request) -> TokenPair:
    return await AuthService(db).refresh(payload.refresh_token, request)


@router.post("/logout", response_model=Message)
async def logout(payload: LogoutRequest, db: DbSession, request: Request) -> Message:
    await AuthService(db).logout(payload.refresh_token, request)
    return Message(message="Logged out")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(payload: UserUpdateSelf, user: CurrentUser, db: DbSession) -> UserOut:
    updated = await UserService(db).update_self(user, payload)
    return UserOut.model_validate(updated)


@router.post("/me/password", response_model=Message)
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, db: DbSession, request: Request
) -> Message:
    # Reuse the registration password policy for new passwords.
    RegisterRequest.model_validate(
        {"email": user.email, "full_name": user.full_name, "password": payload.new_password}
    )
    await UserService(db).change_password(
        user, payload.current_password, payload.new_password, request
    )
    return Message(message="Password updated; other sessions have been signed out")


@router.get("/me/sessions", response_model=list[SessionOut])
async def my_sessions(user: CurrentUser, db: DbSession) -> list[SessionOut]:
    sessions = await UserService(db).list_sessions(user)
    return [SessionOut.model_validate(s) for s in sessions]


@router.delete("/me/sessions/{session_id}", response_model=Message)
async def revoke_session(
    session_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> Message:
    revoked = await AuthService(db).revoke_session(user, session_id)
    if not revoked:
        return Message(message="Session not found")
    return Message(message="Session revoked")
