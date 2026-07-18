"""SP-API client tests: LWA auth, request/response handling, retry, rate limiting.

Uses httpx.MockTransport so these exercise the real HTTP plumbing (headers,
retry loop, envelope unwrapping) with zero network access - no live Amazon
credentials are available in this sandbox.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.amazon.auth import LWATokenProvider
from app.integrations.amazon.client import SPAPIClient
from app.integrations.amazon.exceptions import SPAPIAuthError, SPAPIError
from app.integrations.amazon.rate_limit import RateLimiter, TokenBucket

LWA_OK = httpx.Response(200, json={"access_token": "atza|fake", "expires_in": 3600})


def _client(handler) -> tuple[SPAPIClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SPAPIClient(
        region="NA",
        marketplace_id="ATVPDKIKX0DER",
        lwa_client_id="client-id",
        lwa_client_secret="client-secret",
        lwa_refresh_token="refresh-token",
        seller_id="A1B2C3D4E5",
        http_client=http,
    )
    return client, http


# --- LWA token exchange -------------------------------------------------------------


async def test_lwa_token_fetched_and_cached():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return LWA_OK

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LWATokenProvider(
        client_id="id", client_secret="secret", refresh_token="refresh", http_client=http
    )
    token1 = await provider.get_access_token()
    token2 = await provider.get_access_token()
    assert token1 == token2 == "atza|fake"
    assert len(calls) == 1  # second call served from cache, no re-fetch
    await http.aclose()


async def test_lwa_token_failure_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LWATokenProvider(
        client_id="id", client_secret="secret", refresh_token="bad", http_client=http
    )
    with pytest.raises(SPAPIAuthError):
        await provider.get_access_token()
    await http.aclose()


# --- Sellers / marketplace verification -----------------------------------------------


async def test_list_marketplace_participations():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        assert request.url.path == "/sellers/v1/marketplaceParticipations"
        return httpx.Response(
            200,
            json={
                "payload": [
                    {
                        "marketplace": {
                            "id": "ATVPDKIKX0DER",
                            "name": "Amazon.com",
                            "countryCode": "US",
                            "defaultCurrencyCode": "USD",
                        },
                        "participation": {"isParticipating": True, "hasSuspendedListings": False},
                    }
                ]
            },
        )

    client, http = _client(handler)
    participations = await client.list_marketplace_participations()
    assert len(participations) == 1
    assert participations[0]["marketplace"]["id"] == "ATVPDKIKX0DER"
    assert participations[0]["participation"]["isParticipating"] is True
    await client.aclose()
    await http.aclose()


# --- Orders ------------------------------------------------------------------------


async def test_list_orders_unwraps_payload_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        assert request.url.path == "/orders/v0/orders"
        assert request.headers["x-amz-access-token"] == "atza|fake"
        return httpx.Response(
            200,
            json={
                "payload": {"Orders": [{"AmazonOrderId": "111-1111111-1111111"}], "NextToken": None}
            },
        )

    client, http = _client(handler)
    page = await client.list_orders(created_after=datetime.now(UTC))
    assert len(page.items) == 1
    assert page.items[0]["AmazonOrderId"] == "111-1111111-1111111"
    assert page.next_token is None
    await client.aclose()
    await http.aclose()


async def test_list_orders_pagination_next_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        if "NextToken" in request.url.params:
            return httpx.Response(200, json={"payload": {"Orders": [{"AmazonOrderId": "page2"}]}})
        return httpx.Response(
            200, json={"payload": {"Orders": [{"AmazonOrderId": "page1"}], "NextToken": "tok"}}
        )

    client, http = _client(handler)
    page1 = await client.list_orders(created_after=datetime.now(UTC))
    assert page1.next_token == "tok"
    page2 = await client.list_orders(created_after=datetime.now(UTC), next_token=page1.next_token)
    assert page2.items[0]["AmazonOrderId"] == "page2"
    assert page2.next_token is None
    await client.aclose()
    await http.aclose()


# --- Listings ------------------------------------------------------------------------


async def test_search_listings_items_pages_and_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        assert request.url.path == "/listings/2021-08-01/items/A1B2C3D4E5"
        assert request.url.params["marketplaceIds"] == "ATVPDKIKX0DER"
        if "pageToken" in request.url.params:
            return httpx.Response(
                200,
                json={
                    "numberOfResults": 2,
                    "items": [{"sku": "SKU-2", "summaries": [{"asin": "B000000002"}]}],
                },
            )
        return httpx.Response(
            200,
            json={
                "numberOfResults": 2,
                "pagination": {"nextToken": "page-2"},
                "items": [
                    {
                        "sku": "SKU-1",
                        "summaries": [
                            {
                                "marketplaceId": "ATVPDKIKX0DER",
                                "asin": "B000000001",
                                "productType": "CUTTING_BOARD",
                                "conditionType": "new_new",
                                "status": ["BUYABLE", "DISCOVERABLE"],
                                "itemName": "Bamboo Cutting Board",
                                "createdDate": "2025-01-15T00:00:00Z",
                                "lastUpdatedDate": "2026-06-01T00:00:00Z",
                                "mainImage": {"link": "https://img.example/1.jpg"},
                            }
                        ],
                    }
                ],
            },
        )

    client, http = _client(handler)
    page1 = await client.search_listings_items()
    assert page1.next_token == "page-2"
    assert page1.items[0]["sku"] == "SKU-1"
    assert page1.items[0]["summaries"][0]["status"] == ["BUYABLE", "DISCOVERABLE"]
    page2 = await client.search_listings_items(next_token=page1.next_token)
    assert page2.items[0]["sku"] == "SKU-2"
    assert page2.next_token is None
    await client.aclose()
    await http.aclose()


async def test_search_listings_requires_seller_id():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: LWA_OK))
    client = SPAPIClient(
        region="NA",
        marketplace_id="ATVPDKIKX0DER",
        lwa_client_id="id",
        lwa_client_secret="secret",
        lwa_refresh_token="refresh",
        http_client=http,
    )
    with pytest.raises(SPAPIError):
        await client.search_listings_items()
    await client.aclose()
    await http.aclose()


# --- Retry behavior ------------------------------------------------------------------


async def test_rate_limit_429_then_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"retry-after": "0"}, json={})
        return httpx.Response(200, json={"payload": {"Orders": []}})

    client, http = _client(handler)
    page = await client.list_orders(created_after=datetime.now(UTC))
    assert page.items == []
    assert attempts["n"] == 2
    await client.aclose()
    await http.aclose()


async def test_server_error_retries_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(500, json={"errors": [{"message": "internal"}]})
        return httpx.Response(200, json={"payload": {"Orders": []}})

    client, http = _client(handler)
    page = await client.list_orders(created_after=datetime.now(UTC))
    assert page.items == []
    assert attempts["n"] == 2
    await client.aclose()
    await http.aclose()


async def test_client_error_raises_without_retry():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        attempts["n"] += 1
        return httpx.Response(403, json={"errors": [{"message": "unauthorized"}]})

    client, http = _client(handler)
    with pytest.raises(SPAPIError):
        await client.list_orders(created_after=datetime.now(UTC))
    assert attempts["n"] == 1
    await client.aclose()
    await http.aclose()


# --- Finances flattening --------------------------------------------------------------


async def test_financial_events_are_flattened_and_tagged():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/o2/token":
            return LWA_OK
        return httpx.Response(
            200,
            json={
                "payload": {
                    "FinancialEvents": {
                        "ShipmentEventList": [
                            {
                                "AmazonOrderId": "111-1111111-1111111",
                                "PostedDate": "2026-06-01T00:00:00Z",
                                "ShipmentItemList": [
                                    {
                                        "ItemChargeList": [
                                            {
                                                "ChargeType": "Principal",
                                                "ChargeAmount": {
                                                    "CurrencyAmount": 19.99,
                                                    "CurrencyCode": "USD",
                                                },
                                            }
                                        ]
                                    }
                                ],
                            }
                        ],
                        "ServiceFeeEventList": [
                            {
                                "PostedDate": "2026-06-01T00:00:00Z",
                                "FeeReason": "Subscription",
                                "FeeList": [
                                    {
                                        "FeeType": "Subscription",
                                        "FeeAmount": {
                                            "CurrencyAmount": -39.99,
                                            "CurrencyCode": "USD",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    "NextToken": None,
                }
            },
        )

    client, http = _client(handler)
    page = await client.list_financial_events(posted_after=datetime.now(UTC))
    assert len(page.items) == 2
    types = {item["_event_type"] for item in page.items}
    assert types == {"ShipmentEvent", "ServiceFeeEvent"}
    await client.aclose()
    await http.aclose()


# --- Rate limiter unit behavior ---------------------------------------------------------


async def test_rate_limiter_observes_response_headers():
    limiter = RateLimiter()
    bucket = limiter._bucket("orders.list")
    assert bucket.rate == pytest.approx(0.5)
    limiter.observe_headers("orders.list", {"x-amzn-ratelimit-limit": "2.5"})
    assert bucket.rate == pytest.approx(2.5)


async def test_token_bucket_acquire_refills_over_time():
    bucket = TokenBucket(rate_per_second=1000.0, burst=1.0)
    await bucket.acquire()  # drains the single token
    await bucket.acquire()  # must wait for refill, but refills fast at this rate
