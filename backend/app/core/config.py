"""Application configuration.

All settings are read from environment variables (prefix ``JARVIS_``) or a
``.env`` file. Secrets have safe auto-generated defaults in development but
the application refuses to boot in production without them being set
explicitly.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_name: str = "JARVIS AI"
    api_v1_prefix: str = "/api/v1"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    # --- Database --------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://jarvis:jarvis_dev_password@localhost:5432/jarvis",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- Security --------------------------------------------------------
    jwt_secret_key: str = Field(default="")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    encryption_key: str = Field(default="")
    password_min_length: int = 10
    max_sessions_per_user: int = 10

    # --- CORS ------------------------------------------------------------
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"]
    )

    # --- Rate limiting ---------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120           # general requests per window
    rate_limit_window_seconds: int = 60
    rate_limit_auth_requests: int = 10       # stricter budget for auth endpoints
    rate_limit_auth_window_seconds: int = 60

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _ensure_secrets(self) -> Settings:
        if not self.jwt_secret_key:
            if self.environment == "production":
                raise ValueError(
                    "JARVIS_JWT_SECRET_KEY must be set in production. Generate one "
                    "with: python -c 'import secrets; print(secrets.token_urlsafe(64))'"
                )
            self.jwt_secret_key = secrets.token_urlsafe(64)
        if not self.encryption_key:
            if self.environment == "production":
                raise ValueError(
                    "JARVIS_ENCRYPTION_KEY must be set in production. Generate one with: "
                    "python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                )
            self.encryption_key = Fernet.generate_key().decode()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
