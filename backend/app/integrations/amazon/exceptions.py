"""Amazon SP-API error hierarchy."""

from __future__ import annotations

from app.core.exceptions import JarvisError


class SPAPIError(JarvisError):
    status_code = 502
    code = "amazon_api_error"

    def __init__(self, message: str = "Amazon SP-API request failed") -> None:
        super().__init__(message)


class SPAPIAuthError(SPAPIError):
    code = "amazon_auth_failed"

    def __init__(self, message: str = "Amazon Login-with-Amazon authentication failed") -> None:
        super().__init__(message)


class SPAPIRateLimitError(SPAPIError):
    status_code = 429
    code = "amazon_rate_limited"

    def __init__(self, message: str = "Amazon SP-API rate limit exceeded") -> None:
        super().__init__(message)
