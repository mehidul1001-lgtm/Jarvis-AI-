"""Authentication request/response schemas."""

from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import get_settings

_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"\d")


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=128)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Full name cannot be empty")
        return value

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        min_length = get_settings().password_min_length
        if len(value) < min_length:
            raise ValueError(f"Password must be at least {min_length} characters long")
        if not _UPPER.search(value):
            raise ValueError("Password must contain an uppercase letter")
        if not _LOWER.search(value):
            raise ValueError("Password must contain a lowercase letter")
        if not _DIGIT.search(value):
            raise ValueError("Password must contain a digit")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type label
    expires_in: int
