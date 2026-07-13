"""In-process sliding-window rate limiter.

Suitable for a single-instance deployment; the interface is deliberately
narrow so a Redis-backed implementation can replace :class:`SlidingWindowLimiter`
without touching the middleware when the platform scales horizontally.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = bucket[0] + self.window_seconds - now
                return False, max(retry_after, 0.0)
            bucket.append(now)
            self._maybe_prune(now)
            return True, 0.0

    def _maybe_prune(self, now: float) -> None:
        """Drop idle buckets so memory does not grow with unique client IPs."""
        if now - self._last_prune < self.window_seconds * 5:
            return
        cutoff = now - self.window_seconds
        stale = [key for key, bucket in self._hits.items() if not bucket or bucket[-1] <= cutoff]
        for key in stale:
            del self._hits[key]
        self._last_prune = now

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies a general per-IP budget plus a stricter budget on auth endpoints."""

    AUTH_PREFIXES = ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/refresh")

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        settings = get_settings()
        self.enabled = settings.rate_limit_enabled
        self.general = SlidingWindowLimiter(
            settings.rate_limit_requests, settings.rate_limit_window_seconds
        )
        self.auth = SlidingWindowLimiter(
            settings.rate_limit_auth_requests, settings.rate_limit_auth_window_seconds
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        limiter = self.auth if request.url.path.startswith(self.AUTH_PREFIXES) else self.general
        allowed, retry_after = limiter.allow(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Too many requests, slow down",
                    }
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        return await call_next(request)
