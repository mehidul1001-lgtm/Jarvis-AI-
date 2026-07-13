"""Pulls Amazon SP-API data and upserts it into the local database.

Each ``sync_*`` method is incremental: it reads the resource's saved cursor
from :class:`AmazonSyncState`, asks the API for anything new since then,
paginates to completion, and advances the cursor. Safe to re-run — the
underlying database writes are upserts keyed on Amazon's own identifiers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_value
from app.integrations.amazon.client import AmazonAPI, SPAPIClient
from app.models.amazon import (
    AmazonCredential,
    AmazonFbaShipment,
    AmazonFinancialEvent,
    AmazonInventorySnapshot,
    AmazonOrder,
    AmazonOrderItem,
    AmazonSyncState,
    SyncStatus,
)

logger = logging.getLogger("jarvis.services.amazon_sync")

# Safety cap so a runaway pagination cursor can't loop forever inside one task.
MAX_PAGES_PER_SYNC = 200


@dataclass
class SyncResult:
    resource: str
    created: int = 0
    updated: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated

    def as_dict(self) -> dict[str, Any]:
        return {"resource": self.resource, "created": self.created, "updated": self.updated}


def _parse_amazon_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _sum_currency_amounts(node: Any) -> tuple[Decimal, str | None]:
    """Recursively sum every Amazon ``{"CurrencyAmount": x, "CurrencyCode": c}``
    object found anywhere in a financial event. Event shapes vary widely by
    type, but this "Currency" object shape is used consistently throughout
    SP-API, so a structural walk is more robust than per-type field mapping.
    """
    total = Decimal("0")
    currency: str | None = None
    if isinstance(node, dict):
        if "CurrencyAmount" in node and "CurrencyCode" in node:
            amount = _decimal(node.get("CurrencyAmount"))
            if amount is not None:
                total += amount
                currency = currency or node.get("CurrencyCode")
            return total, currency
        for value in node.values():
            sub_total, sub_currency = _sum_currency_amounts(value)
            total += sub_total
            currency = currency or sub_currency
    elif isinstance(node, list):
        for item in node:
            sub_total, sub_currency = _sum_currency_amounts(item)
            total += sub_total
            currency = currency or sub_currency
    return total, currency


def build_credential_client(credential: AmazonCredential) -> SPAPIClient:
    return SPAPIClient(
        region=credential.region.value,
        marketplace_id=credential.marketplace_id,
        lwa_client_id=credential.lwa_client_id,
        lwa_client_secret=decrypt_value(credential.lwa_client_secret_encrypted),
        lwa_refresh_token=decrypt_value(credential.lwa_refresh_token_encrypted),
    )


class AmazonSyncService:
    def __init__(
        self,
        db: AsyncSession,
        credential: AmazonCredential,
        client: AmazonAPI | None = None,
    ) -> None:
        self.db = db
        self.credential = credential
        self._owns_client = client is None
        self.client = client or build_credential_client(credential)
        self.settings = get_settings()

    async def aclose(self) -> None:
        if self._owns_client and isinstance(self.client, SPAPIClient):
            await self.client.aclose()

    async def __aenter__(self) -> AmazonSyncService:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- Sync state -------------------------------------------------------------

    async def _get_or_create_state(self, resource: str) -> AmazonSyncState:
        result = await self.db.execute(
            select(AmazonSyncState).where(
                AmazonSyncState.credential_id == self.credential.id,
                AmazonSyncState.resource == resource,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = AmazonSyncState(credential_id=self.credential.id, resource=resource)
            self.db.add(state)
            await self.db.flush()
        return state

    def _default_lookback(self) -> datetime:
        return datetime.now(UTC) - timedelta(days=self.settings.amazon_initial_sync_lookback_days)

    # --- Orders -----------------------------------------------------------------

    async def sync_orders(self) -> SyncResult:
        state = await self._get_or_create_state("orders")
        state.status = SyncStatus.RUNNING
        await self.db.flush()

        result = SyncResult(resource="orders")
        cursor_after = state.cursor_after or self._default_lookback()
        latest_seen = cursor_after
        next_token = state.next_token
        logger.info(
            "Amazon sync 'orders' started (credential=%s '%s', created_after=%s)",
            self.credential.id,
            self.credential.label,
            cursor_after.isoformat(),
        )
        try:
            for page_num in range(1, MAX_PAGES_PER_SYNC + 1):
                page = await self.client.list_orders(
                    created_after=cursor_after, next_token=next_token
                )
                logger.info(
                    "Amazon sync 'orders' page %s: %s order(s) (credential=%s)",
                    page_num,
                    len(page.items),
                    self.credential.id,
                )
                for raw in page.items:
                    order, created = await self._upsert_order(raw)
                    if created:
                        result.created += 1
                    else:
                        result.updated += 1
                    items = await self.client.list_order_items(raw["AmazonOrderId"])
                    await self._upsert_order_items(order.id, items)
                    updated_at = _parse_amazon_dt(raw.get("LastUpdateDate")) or _parse_amazon_dt(
                        raw.get("PurchaseDate")
                    )
                    if updated_at and updated_at > latest_seen:
                        latest_seen = updated_at
                next_token = page.next_token
                state.next_token = next_token
                await self.db.flush()
                if not next_token:
                    break
            state.cursor_after = latest_seen
            state.status = SyncStatus.IDLE
            state.last_error = None
            logger.info(
                "Amazon sync 'orders' completed: created=%s updated=%s (credential=%s)",
                result.created,
                result.updated,
                self.credential.id,
            )
        except Exception as exc:
            state.status = SyncStatus.ERROR
            state.last_error = str(exc)[:2000]
            logger.exception(
                "Amazon sync 'orders' failed (credential=%s '%s')",
                self.credential.id,
                self.credential.label,
            )
            raise
        finally:
            state.last_synced_at = datetime.now(UTC)
            state.records_last_sync = result.total
            await self.db.flush()
        return result

    async def _upsert_order(self, raw: dict[str, Any]) -> tuple[AmazonOrder, bool]:
        values = {
            "credential_id": self.credential.id,
            "amazon_order_id": raw["AmazonOrderId"],
            "purchase_date": _parse_amazon_dt(raw.get("PurchaseDate")) or datetime.now(UTC),
            "last_update_date": _parse_amazon_dt(raw.get("LastUpdateDate")) or datetime.now(UTC),
            "order_status": raw.get("OrderStatus", "Unknown"),
            "fulfillment_channel": raw.get("FulfillmentChannel"),
            "sales_channel": raw.get("SalesChannel"),
            "marketplace_id": raw.get("MarketplaceId", self.credential.marketplace_id),
            "number_of_items_shipped": raw.get("NumberOfItemsShipped", 0),
            "number_of_items_unshipped": raw.get("NumberOfItemsUnshipped", 0),
            "order_total_amount": _decimal((raw.get("OrderTotal") or {}).get("Amount")),
            "order_total_currency": (raw.get("OrderTotal") or {}).get("CurrencyCode"),
            "is_business_order": bool(raw.get("IsBusinessOrder", False)),
        }
        stmt = pg_insert(AmazonOrder).values(**values)
        update_cols = {
            k: stmt.excluded[k] for k in values if k not in ("credential_id", "amazon_order_id")
        }
        update_cols["updated_at"] = func.now()
        # xmax = 0 is Postgres's own marker for "this row was just inserted,
        # never updated" - the standard way to tell insert from update apart
        # inside a single ON CONFLICT DO UPDATE statement.
        stmt = stmt.on_conflict_do_update(constraint="uq_amazon_order", set_=update_cols).returning(
            AmazonOrder.id, literal_column("(xmax = 0)").label("inserted")
        )
        row = (await self.db.execute(stmt)).one()
        order = await self.db.get(AmazonOrder, row.id)
        assert order is not None
        return order, bool(row.inserted)

    async def _upsert_order_items(self, order_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
        for raw in items:
            values = {
                "order_id": order_id,
                "order_item_id": raw["OrderItemId"],
                "asin": raw.get("ASIN"),
                "seller_sku": raw.get("SellerSKU"),
                "title": (raw.get("Title") or "")[:500] or None,
                "quantity_ordered": raw.get("QuantityOrdered", 0),
                "quantity_shipped": raw.get("QuantityShipped", 0),
                "item_price_amount": _decimal((raw.get("ItemPrice") or {}).get("Amount")),
                "item_price_currency": (raw.get("ItemPrice") or {}).get("CurrencyCode"),
                "item_tax_amount": _decimal((raw.get("ItemTax") or {}).get("Amount")),
                "promotion_discount_amount": _decimal(
                    (raw.get("PromotionDiscount") or {}).get("Amount")
                ),
            }
            stmt = pg_insert(AmazonOrderItem).values(**values)
            update_cols = {
                k: stmt.excluded[k] for k in values if k not in ("order_id", "order_item_id")
            }
            stmt = stmt.on_conflict_do_update(constraint="uq_amazon_order_item", set_=update_cols)
            await self.db.execute(stmt)

    # --- Inventory ---------------------------------------------------------------

    async def sync_inventory(self) -> SyncResult:
        state = await self._get_or_create_state("inventory")
        state.status = SyncStatus.RUNNING
        await self.db.flush()

        result = SyncResult(resource="inventory")
        snapshot_at = datetime.now(UTC)
        next_token = state.next_token
        logger.info(
            "Amazon sync 'inventory' started (credential=%s '%s')",
            self.credential.id,
            self.credential.label,
        )
        try:
            for page_num in range(1, MAX_PAGES_PER_SYNC + 1):
                page = await self.client.list_inventory_summaries(next_token=next_token)
                logger.info(
                    "Amazon sync 'inventory' page %s: %s summar%s (credential=%s)",
                    page_num,
                    len(page.items),
                    "y" if len(page.items) == 1 else "ies",
                    self.credential.id,
                )
                for raw in page.items:
                    details = raw.get("inventoryDetails") or {}
                    self.db.add(
                        AmazonInventorySnapshot(
                            credential_id=self.credential.id,
                            asin=raw.get("asin"),
                            fnsku=raw.get("fnSku"),
                            seller_sku=raw.get("sellerSku", ""),
                            condition=raw.get("condition"),
                            total_quantity=raw.get("totalQuantity", 0),
                            fulfillable_quantity=details.get("fulfillableQuantity", 0),
                            inbound_working_quantity=details.get("inboundWorkingQuantity", 0),
                            inbound_shipped_quantity=details.get("inboundShippedQuantity", 0),
                            inbound_receiving_quantity=details.get("inboundReceivingQuantity", 0),
                            reserved_quantity=(details.get("reservedQuantity") or {}).get(
                                "totalReservedQuantity", 0
                            ),
                            unfulfillable_quantity=(details.get("unfulfillableQuantity") or {}).get(
                                "totalUnfulfillableQuantity", 0
                            ),
                            snapshot_at=snapshot_at,
                        )
                    )
                    result.created += 1
                next_token = page.next_token
                state.next_token = next_token
                await self.db.flush()
                if not next_token:
                    break
            state.status = SyncStatus.IDLE
            state.last_error = None
            logger.info(
                "Amazon sync 'inventory' completed: %s snapshot(s) written (credential=%s)",
                result.created,
                self.credential.id,
            )
        except Exception as exc:
            state.status = SyncStatus.ERROR
            state.last_error = str(exc)[:2000]
            logger.exception(
                "Amazon sync 'inventory' failed (credential=%s '%s')",
                self.credential.id,
                self.credential.label,
            )
            raise
        finally:
            state.last_synced_at = datetime.now(UTC)
            state.records_last_sync = result.total
            await self.db.flush()
        return result

    # --- FBA inbound shipments -----------------------------------------------------

    async def sync_fba_shipments(self) -> SyncResult:
        state = await self._get_or_create_state("fba_shipments")
        state.status = SyncStatus.RUNNING
        await self.db.flush()

        result = SyncResult(resource="fba_shipments")
        next_token = state.next_token
        logger.info(
            "Amazon sync 'fba_shipments' started (credential=%s '%s')",
            self.credential.id,
            self.credential.label,
        )
        try:
            for page_num in range(1, MAX_PAGES_PER_SYNC + 1):
                page = await self.client.list_inbound_shipments(next_token=next_token)
                logger.info(
                    "Amazon sync 'fba_shipments' page %s: %s shipment(s) (credential=%s)",
                    page_num,
                    len(page.items),
                    self.credential.id,
                )
                for raw in page.items:
                    values = {
                        "credential_id": self.credential.id,
                        "shipment_id": raw["ShipmentId"],
                        "shipment_name": raw.get("ShipmentName"),
                        "destination_fulfillment_center_id": raw.get(
                            "DestinationFulfillmentCenterId"
                        ),
                        "shipment_status": raw.get("ShipmentStatus", "UNKNOWN"),
                        "label_prep_type": raw.get("LabelPrepType"),
                    }
                    stmt = pg_insert(AmazonFbaShipment).values(**values)
                    update_cols = {
                        k: stmt.excluded[k]
                        for k in values
                        if k not in ("credential_id", "shipment_id")
                    }
                    update_cols["updated_at"] = func.now()
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_amazon_fba_shipment", set_=update_cols
                    ).returning(
                        AmazonFbaShipment.id, literal_column("(xmax = 0)").label("inserted")
                    )
                    row = (await self.db.execute(stmt)).one()
                    if row.inserted:
                        result.created += 1
                    else:
                        result.updated += 1
                next_token = page.next_token
                state.next_token = next_token
                await self.db.flush()
                if not next_token:
                    break
            state.status = SyncStatus.IDLE
            state.last_error = None
            logger.info(
                "Amazon sync 'fba_shipments' completed: created=%s updated=%s (credential=%s)",
                result.created,
                result.updated,
                self.credential.id,
            )
        except Exception as exc:
            state.status = SyncStatus.ERROR
            state.last_error = str(exc)[:2000]
            logger.exception(
                "Amazon sync 'fba_shipments' failed (credential=%s '%s')",
                self.credential.id,
                self.credential.label,
            )
            raise
        finally:
            state.last_synced_at = datetime.now(UTC)
            state.records_last_sync = result.total
            await self.db.flush()
        return result

    # --- Financial events -----------------------------------------------------------

    async def sync_financial_events(self) -> SyncResult:
        state = await self._get_or_create_state("financial_events")
        state.status = SyncStatus.RUNNING
        await self.db.flush()

        result = SyncResult(resource="financial_events")
        posted_after = state.cursor_after or self._default_lookback()
        latest_seen = posted_after
        next_token = state.next_token
        logger.info(
            "Amazon sync 'financial_events' started (credential=%s '%s', posted_after=%s)",
            self.credential.id,
            self.credential.label,
            posted_after.isoformat(),
        )
        try:
            for page_num in range(1, MAX_PAGES_PER_SYNC + 1):
                page = await self.client.list_financial_events(
                    posted_after=posted_after, next_token=next_token
                )
                logger.info(
                    "Amazon sync 'financial_events' page %s: %s event(s) (credential=%s)",
                    page_num,
                    len(page.items),
                    self.credential.id,
                )
                for raw in page.items:
                    created = await self._upsert_financial_event(raw)
                    if created:
                        result.created += 1
                    posted = _parse_amazon_dt(raw.get("PostedDate"))
                    if posted and posted > latest_seen:
                        latest_seen = posted
                next_token = page.next_token
                state.next_token = next_token
                await self.db.flush()
                if not next_token:
                    break
            state.cursor_after = latest_seen
            state.status = SyncStatus.IDLE
            state.last_error = None
            logger.info(
                "Amazon sync 'financial_events' completed: %s new event(s) (credential=%s)",
                result.created,
                self.credential.id,
            )
        except Exception as exc:
            state.status = SyncStatus.ERROR
            state.last_error = str(exc)[:2000]
            logger.exception(
                "Amazon sync 'financial_events' failed (credential=%s '%s')",
                self.credential.id,
                self.credential.label,
            )
            raise
        finally:
            state.last_synced_at = datetime.now(UTC)
            state.records_last_sync = result.total
            await self.db.flush()
        return result

    async def _upsert_financial_event(self, raw: dict[str, Any]) -> bool:
        event_type = raw.get("_event_type", "UnknownEvent")
        amount, currency = _sum_currency_amounts(raw)
        posted_date = _parse_amazon_dt(raw.get("PostedDate")) or datetime.now(UTC)
        canonical = json.dumps(
            {k: v for k, v in raw.items() if k != "_event_type"} | {"_event_type": event_type},
            sort_keys=True,
            default=str,
        )
        dedupe_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        values = {
            "credential_id": self.credential.id,
            "event_type": event_type,
            "amazon_order_id": raw.get("AmazonOrderId"),
            "posted_date": posted_date,
            "amount": amount,
            "currency": currency,
            "description": _describe_event(raw, event_type),
            "raw": raw,
            "dedupe_key": dedupe_key,
        }
        stmt = pg_insert(AmazonFinancialEvent).values(**values)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_amazon_financial_event").returning(
            AmazonFinancialEvent.id
        )
        row = (await self.db.execute(stmt)).one_or_none()
        return row is not None

    # --- Orchestration -------------------------------------------------------------

    async def sync_all(self) -> list[SyncResult]:
        return [
            await self.sync_orders(),
            await self.sync_inventory(),
            await self.sync_fba_shipments(),
            await self.sync_financial_events(),
        ]


def _describe_event(raw: dict[str, Any], event_type: str) -> str:
    for key in ("FeeReason", "AdjustmentType", "TransactionType", "PaymentMethod"):
        if raw.get(key):
            return f"{event_type}: {raw[key]}"
    if raw.get("AmazonOrderId"):
        return f"{event_type} for order {raw['AmazonOrderId']}"
    return event_type
