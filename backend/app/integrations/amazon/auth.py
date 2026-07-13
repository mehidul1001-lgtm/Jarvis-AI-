"""Login-with-Amazon (LWA) access token exchange.

Amazon retired the AWS SigV4/IAM-role signing requirement for SP-API in its
2023 migration: every call now only needs a valid LWA access token sent as
the ``x-amz-access-token`` header. This provider exchanges a stored refresh
token for a short-lived (~1h) access token and caches it in memory for the
lifetime of the owning client.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.integrations.amazon.exceptions import SPAPIAuthError

logger = logging.getLogger("jarvis.integrations.amazon")

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"  # noqa: S105 - endpoint URL, not a secret
# Refresh a little before actual expiry to avoid racing a request against it.
EXPIRY_SAFETY_MARGIN_SECONDS = 60


class LWATokenProvider:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._http = http_client
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    async def get_access_token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._expires_at:
            return self._access_token

        try:
            response = await self._http.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise SPAPIAuthError(f"Could not reach Login-with-Amazon: {exc}") from exc

        if response.status_code != 200:
            logger.warning(
                "LWA token refresh failed: %s %s", response.status_code, response.text[:300]
            )
            raise SPAPIAuthError(
                f"Login-with-Amazon refused the refresh token (HTTP {response.status_code})"
            )

        payload = response.json()
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)
        if not access_token:
            raise SPAPIAuthError("Login-with-Amazon response had no access_token")

        self._access_token = access_token
        self._expires_at = time.monotonic() + max(expires_in - EXPIRY_SAFETY_MARGIN_SECONDS, 30)
        return access_token
