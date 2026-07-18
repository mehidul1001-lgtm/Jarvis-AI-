"""Scriptable fake Amazon SP-API client for tests: no network, deterministic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.integrations.amazon.client import AmazonPage


class FakeAmazonClient:
    """Returns queued pages per-operation; records every call it receives."""

    def __init__(self) -> None:
        self._pages: dict[str, list[AmazonPage]] = {
            "orders": [],
            "listings": [],
            "inventory": [],
            "fba_shipments": [],
            "financial_events": [],
        }
        self.order_items: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.marketplace_participations: list[dict[str, Any]] = []

    async def list_marketplace_participations(self) -> list[dict[str, Any]]:
        self.calls.append(("list_marketplace_participations", {}))
        return self.marketplace_participations

    def queue_orders(self, *pages: AmazonPage) -> None:
        self._pages["orders"].extend(pages)

    def queue_listings(self, *pages: AmazonPage) -> None:
        self._pages["listings"].extend(pages)

    def queue_inventory(self, *pages: AmazonPage) -> None:
        self._pages["inventory"].extend(pages)

    def queue_fba_shipments(self, *pages: AmazonPage) -> None:
        self._pages["fba_shipments"].extend(pages)

    def queue_financial_events(self, *pages: AmazonPage) -> None:
        self._pages["financial_events"].extend(pages)

    async def list_orders(
        self, *, created_after: datetime, next_token: str | None = None
    ) -> AmazonPage:
        self.calls.append(
            ("list_orders", {"created_after": created_after, "next_token": next_token})
        )
        pages = self._pages["orders"]
        return pages.pop(0) if pages else AmazonPage()

    async def list_order_items(self, amazon_order_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_order_items", {"amazon_order_id": amazon_order_id}))
        return self.order_items.get(amazon_order_id, [])

    async def search_listings_items(self, *, next_token: str | None = None) -> AmazonPage:
        self.calls.append(("search_listings_items", {"next_token": next_token}))
        pages = self._pages["listings"]
        return pages.pop(0) if pages else AmazonPage()

    async def list_inventory_summaries(self, *, next_token: str | None = None) -> AmazonPage:
        self.calls.append(("list_inventory_summaries", {"next_token": next_token}))
        pages = self._pages["inventory"]
        return pages.pop(0) if pages else AmazonPage()

    async def list_inbound_shipments(self, *, next_token: str | None = None) -> AmazonPage:
        self.calls.append(("list_inbound_shipments", {"next_token": next_token}))
        pages = self._pages["fba_shipments"]
        return pages.pop(0) if pages else AmazonPage()

    async def list_financial_events(
        self, *, posted_after: datetime, next_token: str | None = None
    ) -> AmazonPage:
        self.calls.append(
            ("list_financial_events", {"posted_after": posted_after, "next_token": next_token})
        )
        pages = self._pages["financial_events"]
        return pages.pop(0) if pages else AmazonPage()
