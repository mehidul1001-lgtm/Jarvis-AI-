"""Per-operation token-bucket rate limiting for the SP-API client.

SP-API enforces a distinct rate/burst per operation and returns the exact
current allowance on every response via the ``x-amzn-RateLimit-Limit``
header. Amazon's own guidance is to build an adaptive limiter around that
header rather than hardcode rate cards (which change per-application and
per-seller tier), so buckets start at a conservative default and tighten
or loosen to match whatever the API actually reports.
"""

from __future__ import annotations

import asyncio
import time

# Conservative until an operation reports its real allowance.
DEFAULT_RATE_PER_SECOND = 0.5
DEFAULT_BURST = 2.0


class TokenBucket:
    def __init__(self, rate_per_second: float, burst: float) -> None:
        self.rate = max(rate_per_second, 0.01)
        self.capacity = max(burst, 1.0)
        self._tokens = self.capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    def update_rate(self, rate_per_second: float) -> None:
        if rate_per_second > 0:
            self.rate = rate_per_second

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rate)


class RateLimiter:
    """Keyed collection of token buckets, one per SP-API operation."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, operation: str) -> TokenBucket:
        bucket = self._buckets.get(operation)
        if bucket is None:
            bucket = TokenBucket(DEFAULT_RATE_PER_SECOND, DEFAULT_BURST)
            self._buckets[operation] = bucket
        return bucket

    async def acquire(self, operation: str) -> None:
        await self._bucket(operation).acquire()

    def observe_headers(self, operation: str, headers: dict[str, str]) -> None:
        """Reconcile the bucket with Amazon's reported allowance, if present."""
        raw = headers.get("x-amzn-ratelimit-limit") or headers.get("x-amzn-RateLimit-Limit")
        if not raw:
            return
        try:
            reported_rate = float(raw)
        except ValueError:
            return
        self._bucket(operation).update_rate(reported_rate)
