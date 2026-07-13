"""Password hashing, JWT handling and symmetric encryption utilities."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - token *type* labels, not credentials
REFRESH_TOKEN_TYPE = "refresh"  # noqa: S105


# --- Passwords -----------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# --- Refresh tokens ------------------------------------------------------


def generate_refresh_token() -> str:
    """Opaque, URL-safe random token handed to the client."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Only the SHA-256 digest of a refresh token is stored server-side."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- JWT access tokens ---------------------------------------------------


def create_access_token(
    user_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "sid": str(session_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": expire,
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token") from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise AuthenticationError("Invalid token type")
    return payload


# --- Symmetric encryption for sensitive data at rest ----------------------


def encrypt_value(plaintext: str) -> str:
    fernet = Fernet(get_settings().encryption_key.encode("utf-8"))
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    fernet = Fernet(get_settings().encryption_key.encode("utf-8"))
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt value: invalid key or ciphertext") from exc
