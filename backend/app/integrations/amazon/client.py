"""Amazon Selling Partner API HTTP client.

A thin, provider-neutral layer: callers get back plain dicts/lists (already
unwrapped from whichever response envelope that operation uses), never the
raw ``httpx.Response``. Sync services depend on the :class:`AmazonAPI`
protocol, not this class directly, so tests can substitute a scripted fake
the same way ``LLMClient`` works for the AI brain.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.integrations.amazon.auth import LWATokenProvider
from app.integrations.amazon.exceptions import SPAPIError, SPAPIRateLimitError
from app.integrations.amazon.rate_limit import RateLimiter

logger = logging.getLogger("jarvis.integrations.amazon")

REGION_ENDPOINTS = {
    "NA": "https://sellingpartnerapi-na.amazon.com",
    "EU": "https://sellingpartnerapi-eu.amazon.com",
    "FE": "https://sellingpartnerapi-fe.amazon.com",
}

MAX_ATTEMPTS = 4


@dataclass
class AmazonPage:
    items: list[dict[str, Any]] = field(default_factory=list)
    next_token: str | None = None


class AmazonAPI(Protocol):
    async def list_marketplace_participations(self) -> list[dict[str, Any]]: ...

    async def list_orders(
        self, *, created_after: datetime, next_token: str | None = None
    ) -> AmazonPage: ...

    async def list_order_items(self, amazon_order_id: str) -> list[dict[str, Any]]: ...

    async def search_listings_items(self, *, next_token: str | None = None) -> AmazonPage: ...

    async def list_inventory_summaries(self, *, next_token: str | None = None) -> AmazonPage: ...

    async def list_inbound_shipments(self, *, next_token: str | None = None) -> AmazonPage: ...

    async def list_financial_events(
        self, *, posted_after: datetime, next_token: str | None = None
    ) -> AmazonPage: ...


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class SPAPIClient:
    """Real SP-API client. One instance per sync run (short-lived)."""

    def __init__(
        self,
        *,
        region: str,
        marketplace_id: str,
        lwa_client_id: str,
        lwa_client_secret: str,
        lwa_refresh_token: str,
        # Only the Listings API embeds the seller id in its URL path;
        # optional so auth/marketplace diagnostics work without it.
        seller_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if region not in REGION_ENDPOINTS:
            raise SPAPIError(f"Unknown SP-API region '{region}'")
        self.base_url = REGION_ENDPOINTS[region]
        self.marketplace_id = marketplace_id
        self.seller_id = seller_id
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._token = LWATokenProvider(
            client_id=lwa_client_id,
            client_secret=lwa_client_secret,
            refresh_token=lwa_refresh_token,
            http_client=self._http,
        )
        self._rate_limiter = RateLimiter()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> SPAPIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def verify_authentication(self) -> str:
        """Exchanges the refresh token for an access token without making any
        other API call - isolates auth failures from data-endpoint failures
        when diagnosing a newly connected account."""
        return await self._token.get_access_token()

    # --- Low-level request plumbing -----------------------------------------

    async def _request(
        self, operation: str, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        started = asyncio.get_event_loop().time()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._rate_limiter.acquire(operation)
            try:
                access_token = await self._token.get_access_token()
            except Exception:
                logger.error("SP-API '%s': could not obtain an LWA access token", operation)
                raise
            try:
                response = await self._http.request(
                    method,
                    f"{self.base_url}{path}",
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    headers={
                        "x-amz-access-token": access_token,
                        "accept": "application/json",
                    },
                )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "SP-API '%s' connection error (attempt %s/%s): %s; retrying",
                    operation,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(2 * attempt)
                continue

            self._rate_limiter.observe_headers(operation, dict(response.headers))
            request_id = response.headers.get("x-amzn-requestid")

            if response.status_code == 429:
                retry_after = float(response.headers.get("retry-after", 2 * attempt))
                last_error = SPAPIRateLimitError()
                logger.warning(
                    "SP-API '%s' rate limited (attempt %s/%s, request-id=%s); retrying in %.1fs",
                    operation,
                    attempt,
                    MAX_ATTEMPTS,
                    request_id,
                    retry_after,
                )
                await asyncio.sleep(min(retry_after, 30))
                continue
            if response.status_code >= 500:
                last_error = SPAPIError(
                    f"SP-API server error {response.status_code} on {operation}"
                )
                logger.warning(
                    "SP-API '%s' server error %s (attempt %s/%s, request-id=%s); retrying",
                    operation,
                    response.status_code,
                    attempt,
                    MAX_ATTEMPTS,
                    request_id,
                )
                await asyncio.sleep(2 * attempt)
                continue
            if response.status_code >= 400:
                detail = response.text[:500]
                logger.error(
                    "SP-API '%s' rejected: HTTP %s (request-id=%s): %s",
                    operation,
                    response.status_code,
                    request_id,
                    detail,
                )
                raise SPAPIError(
                    f"SP-API rejected {operation} (HTTP {response.status_code}): {detail}"
                )

            elapsed_ms = round((asyncio.get_event_loop().time() - started) * 1000, 1)
            logger.info(
                "SP-API '%s' succeeded (attempt %s/%s, %sms, request-id=%s)",
                operation,
                attempt,
                MAX_ATTEMPTS,
                elapsed_ms,
                request_id,
            )
            return response.json()

        logger.error(
            "SP-API '%s' failed after %s attempts: %s", operation, MAX_ATTEMPTS, last_error
        )
        raise SPAPIError(
            f"SP-API request '{operation}' failed after {MAX_ATTEMPTS} attempts"
        ) from (last_error)

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        """SP-API v0 endpoints wrap the body in {"payload": ...}; v1 endpoints
        (e.g. FBA Inventory) return the body directly. Handle both."""
        return payload.get("payload", payload)

    # --- Sellers v1 ------------------------------------------------------------

    async def list_marketplace_participations(self) -> list[dict[str, Any]]:
        """Which marketplaces these credentials are actually authorized for -
        the documented way to confirm auth + marketplace/region are correct
        before trusting any other endpoint's data."""
        raw = await self._request(
            "sellers.marketplace_participations", "GET", "/sellers/v1/marketplaceParticipations"
        )
        payload = raw.get("payload", raw)
        return payload if isinstance(payload, list) else []

    # --- Orders v0 -----------------------------------------------------------

    async def list_orders(
        self, *, created_after: datetime, next_token: str | None = None
    ) -> AmazonPage:
        params: dict[str, Any] = {"MarketplaceIds": self.marketplace_id}
        if next_token:
            params["NextToken"] = next_token
        else:
            params["CreatedAfter"] = _iso(created_after)
        raw = self._unwrap(
            await self._request("orders.list", "GET", "/orders/v0/orders", params=params)
        )
        return AmazonPage(items=raw.get("Orders", []), next_token=raw.get("NextToken"))

    async def list_order_items(self, amazon_order_id: str) -> list[dict[str, Any]]:
        raw = self._unwrap(
            await self._request(
                "orders.items", "GET", f"/orders/v0/orders/{amazon_order_id}/orderItems"
            )
        )
        items = list(raw.get("OrderItems", []))
        next_token = raw.get("NextToken")
        while next_token:
            raw = self._unwrap(
                await self._request(
                    "orders.items",
                    "GET",
                    f"/orders/v0/orders/{amazon_order_id}/orderItems",
                    params={"NextToken": next_token},
                )
            )
            items.extend(raw.get("OrderItems", []))
            next_token = raw.get("NextToken")
        return items

    # --- Listings Items 2021-08-01 ----------------------------------------------

    async def search_listings_items(self, *, next_token: str | None = None) -> AmazonPage:
        """All of the seller's listings in this marketplace, one page at a time.

        Unlike the v0 endpoints this API is not payload-wrapped and pages via
        ``pageToken``; its maximum page size is 20.
        """
        if not self.seller_id:
            raise SPAPIError("seller_id is required for the Listings API")
        params: dict[str, Any] = {
            "marketplaceIds": self.marketplace_id,
            "includedData": "summaries",
            "pageSize": 20,
        }
        if next_token:
            params["pageToken"] = next_token
        raw = await self._request(
            "listings.search",
            "GET",
            f"/listings/2021-08-01/items/{self.seller_id}",
            params=params,
        )
        pagination = raw.get("pagination") or {}
        return AmazonPage(items=raw.get("items", []), next_token=pagination.get("nextToken"))

    # --- FBA Inventory v1 ------------------------------------------------------

    async def list_inventory_summaries(self, *, next_token: str | None = None) -> AmazonPage:
        params: dict[str, Any] = {
            "granularityType": "Marketplace",
            "granularityId": self.marketplace_id,
            "marketplaceIds": self.marketplace_id,
            "details": "true",
        }
        if next_token:
            params["nextToken"] = next_token
        raw = await self._request(
            "inventory.list", "GET", "/fba/inventory/v1/summaries", params=params
        )
        payload = self._unwrap(raw)
        pagination = payload.get("pagination") or raw.get("pagination") or {}
        return AmazonPage(
            items=payload.get("inventorySummaries", []),
            next_token=pagination.get("nextToken"),
        )

    # --- FBA Inbound (Fulfillment Inbound) v0 -----------------------------------

    async def list_inbound_shipments(self, *, next_token: str | None = None) -> AmazonPage:
        params: dict[str, Any] = {"MarketplaceId": self.marketplace_id}
        if next_token:
            params["QueryType"] = "NEXT_TOKEN"
            params["NextToken"] = next_token
        else:
            params["QueryType"] = "DATE_RANGE"
            params["ShipmentStatusList"] = (
                "WORKING,SHIPPED,IN_TRANSIT,DELIVERED,CHECKED_IN,RECEIVING,CLOSED"
            )
        raw = self._unwrap(
            await self._request(
                "fba_inbound.list", "GET", "/fba/inbound/v0/shipments", params=params
            )
        )
        return AmazonPage(items=raw.get("ShipmentData", []), next_token=raw.get("NextToken"))

    # --- Finances v0 -----------------------------------------------------------

    async def list_financial_events(
        self, *, posted_after: datetime, next_token: str | None = None
    ) -> AmazonPage:
        params: dict[str, Any] = {}
        if next_token:
            params["NextToken"] = next_token
        else:
            params["PostedAfter"] = _iso(posted_after)
        raw = self._unwrap(
            await self._request(
                "finances.list", "GET", "/finances/v0/financialEvents", params=params
            )
        )
        events_by_type = raw.get("FinancialEvents", {}) or {}
        # FinancialEvents groups heterogeneous event kinds under keys like
        # "ShipmentEventList"/"RefundEventList"/"ServiceFeeEventList". Flatten
        # them into one list, tagging each item with its source category so
        # the sync service doesn't need to special-case every key by hand.
        flattened: list[dict[str, Any]] = []
        for key, events in events_by_type.items():
            if not key.endswith("EventList") or not isinstance(events, list):
                continue
            event_type = key[: -len("List")]
            for event in events:
                flattened.append({**event, "_event_type": event_type})
        return AmazonPage(items=flattened, next_token=raw.get("NextToken"))
