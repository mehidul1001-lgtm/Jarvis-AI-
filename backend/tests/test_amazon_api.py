"""Amazon API tests: credential RBAC/secrecy, data endpoints, sync trigger,
and one full end-to-end run through the real workflow engine with the
SP-API network call swapped for a FakeAmazonClient.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import db
from app.integrations.amazon.client import AmazonPage
from app.models.amazon import (
    AmazonCredential,
    AmazonFinancialEvent,
    AmazonInventorySnapshot,
    AmazonListing,
    AmazonOrder,
)
from app.models.task import TaskStatus, WorkflowTask
from app.workflows.engine import get_workflow_engine
from tests.conftest import auth_header
from tests.fake_amazon import FakeAmazonClient

CREDENTIAL_PAYLOAD = {
    "label": "Primary Store",
    "region": "NA",
    "marketplace_id": "ATVPDKIKX0DER",
    "seller_id": "A1B2C3D4E5",
    "lwa_client_id": "amzn1.application-oa2-client.fake",
    "lwa_client_secret": "super-secret-value",
    "lwa_refresh_token": "Atzr|super-secret-refresh-token",
}


@pytest.fixture
async def engine(client):
    engine = get_workflow_engine()
    engine.retry_base_delay = 0.05
    await engine.start()
    yield engine
    await engine.stop()


async def _wait_status(task_id, statuses, timeout=8.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db.sessionmaker() as session:
            task = await session.get(WorkflowTask, task_id)
            if task and task.status in statuses:
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"task never reached {statuses}")


# --- Credential RBAC and secrecy ------------------------------------------------------


async def test_connect_account_requires_manager_or_admin(
    client: AsyncClient, admin_tokens, user_tokens
):
    resp = await client.post(
        "/api/v1/amazon/credentials", json=CREDENTIAL_PAYLOAD, headers=auth_header(user_tokens)
    )
    assert resp.status_code == 403

    resp = await client.post(
        "/api/v1/amazon/credentials", json=CREDENTIAL_PAYLOAD, headers=auth_header(admin_tokens)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["label"] == "Primary Store"
    assert "lwa_client_secret" not in body
    assert "lwa_refresh_token" not in body
    assert "lwa_client_secret_encrypted" not in body


async def test_credentials_are_encrypted_at_rest(client: AsyncClient, admin_tokens):
    resp = await client.post(
        "/api/v1/amazon/credentials", json=CREDENTIAL_PAYLOAD, headers=auth_header(admin_tokens)
    )
    credential_id = resp.json()["id"]
    async with db.sessionmaker() as session:
        credential = await session.get(AmazonCredential, uuid.UUID(credential_id))
        assert credential.lwa_client_secret_encrypted != CREDENTIAL_PAYLOAD["lwa_client_secret"]
        assert credential.lwa_refresh_token_encrypted != CREDENTIAL_PAYLOAD["lwa_refresh_token"]


async def test_list_and_disconnect_account(client: AsyncClient, admin_tokens):
    resp = await client.post(
        "/api/v1/amazon/credentials", json=CREDENTIAL_PAYLOAD, headers=auth_header(admin_tokens)
    )
    credential_id = resp.json()["id"]

    resp = await client.get("/api/v1/amazon/credentials", headers=auth_header(admin_tokens))
    assert any(c["id"] == credential_id for c in resp.json())

    resp = await client.delete(
        f"/api/v1/amazon/credentials/{credential_id}", headers=auth_header(admin_tokens)
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/amazon/credentials", headers=auth_header(admin_tokens))
    assert all(c["id"] != credential_id for c in resp.json())


# --- Data endpoints reflect synced rows ------------------------------------------------


@pytest.fixture
async def seeded_credential(client: AsyncClient, admin_tokens) -> str:
    resp = await client.post(
        "/api/v1/amazon/credentials", json=CREDENTIAL_PAYLOAD, headers=auth_header(admin_tokens)
    )
    credential_id = resp.json()["id"]

    async with db.sessionmaker() as session:
        cred_uuid = uuid.UUID(credential_id)
        session.add(
            AmazonOrder(
                credential_id=cred_uuid,
                amazon_order_id="777-0000000-0000001",
                purchase_date=datetime.now(UTC),
                last_update_date=datetime.now(UTC),
                order_status="Shipped",
                marketplace_id="ATVPDKIKX0DER",
                order_total_amount="49.99",
                order_total_currency="USD",
            )
        )
        session.add(
            AmazonInventorySnapshot(
                credential_id=cred_uuid,
                asin="B000000002",
                seller_sku="SKU-LOW",
                total_quantity=3,
                fulfillable_quantity=3,
                snapshot_at=datetime.now(UTC),
            )
        )
        session.add(
            AmazonListing(
                credential_id=cred_uuid,
                seller_sku="SKU-LOW",
                asin="B000000002",
                item_name="Bamboo Cutting Board",
                status=["BUYABLE"],
            )
        )
        session.add(
            AmazonFinancialEvent(
                credential_id=cred_uuid,
                event_type="ShipmentEvent",
                amazon_order_id="777-0000000-0000001",
                posted_date=datetime.now(UTC),
                amount="49.99",
                currency="USD",
                description="ShipmentEvent for order 777-0000000-0000001",
                raw={"_event_type": "ShipmentEvent"},
                dedupe_key="test-dedupe-key-1",
            )
        )
        await session.commit()
    return credential_id


async def test_orders_endpoint_returns_seeded_order(
    client: AsyncClient, admin_tokens, seeded_credential
):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/orders", headers=auth_header(admin_tokens)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["amazon_order_id"] == "777-0000000-0000001"


async def test_sales_summary_reflects_order_total(
    client: AsyncClient, admin_tokens, seeded_credential
):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/orders/summary",
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_count"] == 1
    assert body["total_revenue"] == "49.99"


async def test_listings_endpoint_returns_seeded_listing(
    client: AsyncClient, admin_tokens, seeded_credential
):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/listings",
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["seller_sku"] == "SKU-LOW"
    assert body["items"][0]["status"] == ["BUYABLE"]
    assert body["items"][0]["item_name"] == "Bamboo Cutting Board"


async def test_inventory_low_stock_filter(client: AsyncClient, admin_tokens, seeded_credential):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/inventory",
        params={"low_stock_only": True},
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["seller_sku"] == "SKU-LOW"


async def test_dashboard_aggregates_sales_and_low_stock(
    client: AsyncClient, admin_tokens, seeded_credential
):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/dashboard",
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sales"]["order_count"] == 1
    assert len(body["low_stock"]) == 1
    assert body["financials"]["net_amount"] == "49.99"


async def test_cannot_access_another_users_credential(
    client: AsyncClient, admin_tokens, user_tokens, seeded_credential
):
    resp = await client.get(
        f"/api/v1/amazon/credentials/{seeded_credential}/orders", headers=auth_header(user_tokens)
    )
    assert resp.status_code == 404


# --- Sync trigger + end-to-end workflow run --------------------------------------------


async def test_trigger_sync_creates_task(client: AsyncClient, admin_tokens, seeded_credential):
    resp = await client.post(
        f"/api/v1/amazon/credentials/{seeded_credential}/sync",
        json={"resource": "orders"},
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == "orders"

    resp = await client.get(f"/api/v1/tasks/{body['task_id']}", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    assert resp.json()["handler"] == "amazon.sync"


async def test_amazon_sync_runs_end_to_end_and_reschedules(
    client: AsyncClient, admin_tokens, seeded_credential, engine
):
    fake = FakeAmazonClient()
    fake.queue_orders(
        AmazonPage(
            items=[
                {
                    "AmazonOrderId": "888-0000000-0000001",
                    "PurchaseDate": "2026-06-01T00:00:00Z",
                    "LastUpdateDate": "2026-06-01T00:00:00Z",
                    "OrderStatus": "Shipped",
                    "MarketplaceId": "ATVPDKIKX0DER",
                    "OrderTotal": {"Amount": "12.34", "CurrencyCode": "USD"},
                }
            ]
        )
    )
    fake.order_items["888-0000000-0000001"] = []

    with patch("app.services.amazon_sync_service.build_credential_client", return_value=fake):
        resp = await client.post(
            f"/api/v1/amazon/credentials/{seeded_credential}/sync",
            json={"resource": "orders", "recurring": True},
            headers=auth_header(admin_tokens),
        )
        task_id = resp.json()["task_id"]
        task = await _wait_status(task_id, {TaskStatus.COMPLETED, TaskStatus.FAILED})

    assert task.status == TaskStatus.COMPLETED, task.error
    assert task.result["results"][0]["created"] == 1

    async with db.sessionmaker() as session:
        order = (
            await session.execute(
                select(AmazonOrder).where(AmazonOrder.amazon_order_id == "888-0000000-0000001")
            )
        ).scalar_one()
        assert str(order.order_total_amount) == "12.34"

        rescheduled = (
            (
                await session.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.handler == "amazon.sync",
                        WorkflowTask.id != task.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rescheduled) == 1
        assert rescheduled[0].scheduled_for is not None
        assert rescheduled[0].scheduled_for > datetime.now(UTC)
