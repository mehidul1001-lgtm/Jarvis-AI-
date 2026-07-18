"""AmazonSyncService tests: upsert idempotency, pagination, cursors, dedup.

Drives the real service and Postgres against a scripted FakeAmazonClient -
no network, but every upsert/query is real SQL.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import db
from app.core.security import encrypt_value
from app.integrations.amazon.client import AmazonPage
from app.models.amazon import (
    AmazonCredential,
    AmazonFbaShipment,
    AmazonFinancialEvent,
    AmazonInventorySnapshot,
    AmazonListing,
    AmazonOrder,
    AmazonRegion,
    AmazonSyncState,
    SyncStatus,
)
from app.models.user import User
from app.services.amazon_sync_service import AmazonSyncService
from tests.fake_amazon import FakeAmazonClient


async def _admin() -> User:
    async with db.sessionmaker() as session:
        return (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()


@pytest.fixture
async def credential(client, admin_tokens) -> AmazonCredential:
    user = await _admin()
    async with db.sessionmaker() as session:
        cred = AmazonCredential(
            user_id=user.id,
            label="Test Store",
            region=AmazonRegion.NA,
            marketplace_id="ATVPDKIKX0DER",
            seller_id="A1B2C3D4E5",
            lwa_client_id="amzn1.application-oa2-client.fake",
            lwa_client_secret_encrypted=encrypt_value("fake-secret"),
            lwa_refresh_token_encrypted=encrypt_value("Atzr|fake-refresh"),
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)
        return cred


def _order(order_id: str, total: str = "25.00") -> dict:
    return {
        "AmazonOrderId": order_id,
        "PurchaseDate": "2026-06-01T12:00:00Z",
        "LastUpdateDate": "2026-06-01T12:05:00Z",
        "OrderStatus": "Shipped",
        "FulfillmentChannel": "AFN",
        "MarketplaceId": "ATVPDKIKX0DER",
        "NumberOfItemsShipped": 1,
        "NumberOfItemsUnshipped": 0,
        "OrderTotal": {"Amount": total, "CurrencyCode": "USD"},
    }


def _order_item(item_id: str = "item-1") -> dict:
    return {
        "OrderItemId": item_id,
        "ASIN": "B000000001",
        "SellerSKU": "SKU-1",
        "Title": "Bamboo Cutting Board",
        "QuantityOrdered": 1,
        "QuantityShipped": 1,
        "ItemPrice": {"Amount": "25.00", "CurrencyCode": "USD"},
    }


# --- Orders -----------------------------------------------------------------------


async def test_sync_orders_creates_orders_and_items(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_orders(AmazonPage(items=[_order("111-1111111-1111111")], next_token=None))
        fake.order_items["111-1111111-1111111"] = [_order_item()]

        service = AmazonSyncService(session, credential, fake)
        result = await service.sync_orders()
        await session.commit()

        assert result.created == 1
        assert result.updated == 0

        order = (
            await session.execute(
                select(AmazonOrder)
                .where(AmazonOrder.amazon_order_id == "111-1111111-1111111")
                .options(selectinload(AmazonOrder.items))
            )
        ).scalar_one()
        assert order.order_status == "Shipped"
        assert str(order.order_total_amount) == "25.00"
        assert len(order.items) == 1
        assert order.items[0].seller_sku == "SKU-1"

        state = (
            await session.execute(
                select(AmazonSyncState).where(
                    AmazonSyncState.credential_id == credential.id,
                    AmazonSyncState.resource == "orders",
                )
            )
        ).scalar_one()
        assert state.status == SyncStatus.IDLE
        assert state.next_token is None
        assert state.cursor_after is not None


async def test_sync_orders_upsert_is_idempotent(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_orders(AmazonPage(items=[_order("222-2222222-2222222")]))
        fake.order_items["222-2222222-2222222"] = [_order_item()]
        result1 = await AmazonSyncService(session, credential, fake).sync_orders()
        await session.commit()
        assert result1.created == 1

        # Re-sync the same order with an updated status - must update, not duplicate.
        updated_order = _order("222-2222222-2222222")
        updated_order["OrderStatus"] = "Delivered"
        fake2 = FakeAmazonClient()
        fake2.queue_orders(AmazonPage(items=[updated_order]))
        fake2.order_items["222-2222222-2222222"] = [_order_item()]
        result2 = await AmazonSyncService(session, credential, fake2).sync_orders()
        await session.commit()
        assert result2.created == 0
        assert result2.updated == 1

        count = (
            (
                await session.execute(
                    select(AmazonOrder).where(AmazonOrder.amazon_order_id == "222-2222222-2222222")
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1
        assert count[0].order_status == "Delivered"


async def test_sync_orders_paginates_until_next_token_exhausted(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_orders(
            AmazonPage(items=[_order("333-0000000-0000001")], next_token="page-2"),
            AmazonPage(items=[_order("333-0000000-0000002")], next_token=None),
        )
        fake.order_items["333-0000000-0000001"] = []
        fake.order_items["333-0000000-0000002"] = []

        result = await AmazonSyncService(session, credential, fake).sync_orders()
        await session.commit()

        assert result.created == 2
        list_calls = [c for c in fake.calls if c[0] == "list_orders"]
        assert len(list_calls) == 2
        assert list_calls[1][1]["next_token"] == "page-2"


# --- Listings ----------------------------------------------------------------------


def _listing(sku: str, status: list[str] | None = None) -> dict:
    return {
        "sku": sku,
        "summaries": [
            {
                "marketplaceId": "ATVPDKIKX0DER",
                "asin": "B000000001",
                "productType": "CUTTING_BOARD",
                "conditionType": "new_new",
                "status": status if status is not None else ["BUYABLE"],
                "itemName": "Bamboo Cutting Board",
                "createdDate": "2025-01-15T00:00:00Z",
                "lastUpdatedDate": "2026-06-01T00:00:00Z",
                "mainImage": {"link": "https://img.example/1.jpg"},
            }
        ],
    }


async def test_sync_listings_creates_and_paginates(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_listings(
            AmazonPage(items=[_listing("SKU-A")], next_token="page-2"),
            AmazonPage(items=[_listing("SKU-B")], next_token=None),
        )
        result = await AmazonSyncService(session, credential, fake).sync_listings()
        await session.commit()

        assert result.created == 2
        rows = (
            (
                await session.execute(
                    select(AmazonListing)
                    .where(AmazonListing.credential_id == credential.id)
                    .order_by(AmazonListing.seller_sku)
                )
            )
            .scalars()
            .all()
        )
        assert [r.seller_sku for r in rows] == ["SKU-A", "SKU-B"]
        assert rows[0].asin == "B000000001"
        assert rows[0].status == ["BUYABLE"]
        assert rows[0].item_name == "Bamboo Cutting Board"


async def test_sync_listings_upsert_is_idempotent(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_listings(AmazonPage(items=[_listing("SKU-C")]))
        result1 = await AmazonSyncService(session, credential, fake).sync_listings()
        await session.commit()
        assert result1.created == 1

        # Re-sync with a changed status - must update in place, not duplicate.
        fake2 = FakeAmazonClient()
        fake2.queue_listings(AmazonPage(items=[_listing("SKU-C", status=[])]))
        result2 = await AmazonSyncService(session, credential, fake2).sync_listings()
        await session.commit()
        assert result2.created == 0
        assert result2.updated == 1

        rows = (
            (
                await session.execute(
                    select(AmazonListing).where(AmazonListing.seller_sku == "SKU-C")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == []


# --- Inventory ---------------------------------------------------------------------


async def test_sync_inventory_appends_snapshot(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_inventory(
            AmazonPage(
                items=[
                    {
                        "asin": "B000000001",
                        "fnSku": "X001",
                        "sellerSku": "SKU-1",
                        "condition": "NewItem",
                        "totalQuantity": 42,
                        "inventoryDetails": {
                            "fulfillableQuantity": 40,
                            "inboundWorkingQuantity": 0,
                            "inboundShippedQuantity": 2,
                            "inboundReceivingQuantity": 0,
                            "reservedQuantity": {"totalReservedQuantity": 0},
                            "unfulfillableQuantity": {"totalUnfulfillableQuantity": 0},
                        },
                    }
                ]
            )
        )
        result = await AmazonSyncService(session, credential, fake).sync_inventory()
        await session.commit()

        assert result.created == 1
        snapshot = (
            await session.execute(
                select(AmazonInventorySnapshot).where(
                    AmazonInventorySnapshot.credential_id == credential.id
                )
            )
        ).scalar_one()
        assert snapshot.seller_sku == "SKU-1"
        assert snapshot.fulfillable_quantity == 40
        assert snapshot.total_quantity == 42


# --- FBA shipments -----------------------------------------------------------------


async def test_sync_fba_shipments_upserts(credential):
    async with db.sessionmaker() as session:
        shipment = {
            "ShipmentId": "FBA1ABCDEF",
            "ShipmentName": "June Restock",
            "DestinationFulfillmentCenterId": "PHX3",
            "ShipmentStatus": "WORKING",
        }
        fake = FakeAmazonClient()
        fake.queue_fba_shipments(AmazonPage(items=[shipment]))
        result1 = await AmazonSyncService(session, credential, fake).sync_fba_shipments()
        await session.commit()
        assert result1.created == 1

        shipment["ShipmentStatus"] = "SHIPPED"
        fake2 = FakeAmazonClient()
        fake2.queue_fba_shipments(AmazonPage(items=[shipment]))
        result2 = await AmazonSyncService(session, credential, fake2).sync_fba_shipments()
        await session.commit()
        assert result2.created == 0
        assert result2.updated == 1

        rows = (
            (
                await session.execute(
                    select(AmazonFbaShipment).where(
                        AmazonFbaShipment.credential_id == credential.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].shipment_status == "SHIPPED"


# --- Financial events -----------------------------------------------------------------


def _shipment_event(order_id: str, amount: str = "19.99") -> dict:
    return {
        "_event_type": "ShipmentEvent",
        "AmazonOrderId": order_id,
        "PostedDate": "2026-06-01T00:00:00Z",
        "ShipmentItemList": [
            {
                "ItemChargeList": [
                    {
                        "ChargeType": "Principal",
                        "ChargeAmount": {"CurrencyAmount": amount, "CurrencyCode": "USD"},
                    }
                ]
            }
        ],
    }


async def test_sync_financial_events_extracts_amount(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        fake.queue_financial_events(
            AmazonPage(items=[_shipment_event("444-0000000-0000001", "19.99")])
        )
        result = await AmazonSyncService(session, credential, fake).sync_financial_events()
        await session.commit()

        assert result.created == 1
        event = (
            await session.execute(
                select(AmazonFinancialEvent).where(
                    AmazonFinancialEvent.credential_id == credential.id
                )
            )
        ).scalar_one()
        assert event.event_type == "ShipmentEvent"
        assert str(event.amount) == "19.99"
        assert event.currency == "USD"


async def test_sync_financial_events_dedupes_identical_events(credential):
    async with db.sessionmaker() as session:
        event_payload = _shipment_event("555-0000000-0000001", "9.99")

        fake1 = FakeAmazonClient()
        fake1.queue_financial_events(AmazonPage(items=[event_payload]))
        result1 = await AmazonSyncService(session, credential, fake1).sync_financial_events()
        await session.commit()
        assert result1.created == 1

        # Re-syncing an overlapping window returns the identical event again.
        fake2 = FakeAmazonClient()
        fake2.queue_financial_events(AmazonPage(items=[dict(event_payload)]))
        result2 = await AmazonSyncService(session, credential, fake2).sync_financial_events()
        await session.commit()
        assert result2.created == 0

        rows = (
            (
                await session.execute(
                    select(AmazonFinancialEvent).where(
                        AmazonFinancialEvent.credential_id == credential.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


# --- Orchestration -------------------------------------------------------------------


async def test_sync_all_runs_every_resource(credential):
    async with db.sessionmaker() as session:
        fake = FakeAmazonClient()
        results = await AmazonSyncService(session, credential, fake).sync_all()
        await session.commit()
        assert {r.resource for r in results} == {
            "orders",
            "listings",
            "inventory",
            "fba_shipments",
            "financial_events",
        }
