"""Read/write access to connected Amazon accounts and their synced data.

Every query is scoped through a credential the caller owns — there is no
cross-user access to another account's Amazon data, mirroring the
ownership checks already used for tasks/conversations/memories.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.core.security import encrypt_value
from app.models.amazon import (
    AmazonCredential,
    AmazonFbaShipment,
    AmazonFinancialEvent,
    AmazonInventorySnapshot,
    AmazonListing,
    AmazonOrder,
    AmazonSyncState,
)
from app.models.user import User
from app.schemas.amazon import AmazonCredentialCreate
from app.services.audit_service import AuditService

LOW_STOCK_THRESHOLD = 10


class AmazonDataService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    # --- Credentials -------------------------------------------------------------

    async def create_credential(self, user: User, data: AmazonCredentialCreate) -> AmazonCredential:
        credential = AmazonCredential(
            user_id=user.id,
            label=data.label,
            region=data.region,
            marketplace_id=data.marketplace_id,
            seller_id=data.seller_id,
            lwa_client_id=data.lwa_client_id,
            lwa_client_secret_encrypted=encrypt_value(data.lwa_client_secret),
            lwa_refresh_token_encrypted=encrypt_value(data.lwa_refresh_token),
        )
        self.db.add(credential)
        await self.db.flush()
        await self.audit.record(
            "amazon.credential_connected",
            user_id=user.id,
            resource=f"amazon_credential:{credential.id}",
            detail={"label": credential.label, "region": credential.region.value},
        )
        return credential

    async def list_credentials(self, user: User) -> list[AmazonCredential]:
        result = await self.db.execute(
            select(AmazonCredential)
            .where(AmazonCredential.user_id == user.id)
            .order_by(AmazonCredential.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_credential(self, user: User, credential_id: uuid.UUID) -> AmazonCredential:
        credential = await self.db.get(AmazonCredential, credential_id)
        if credential is None or credential.user_id != user.id:
            raise NotFoundError("Amazon credential not found")
        return credential

    async def delete_credential(self, user: User, credential_id: uuid.UUID) -> None:
        credential = await self.get_credential(user, credential_id)
        await self.db.delete(credential)
        await self.audit.record(
            "amazon.credential_disconnected",
            user_id=user.id,
            resource=f"amazon_credential:{credential_id}",
        )

    async def sync_states(self, user: User, credential_id: uuid.UUID) -> list[AmazonSyncState]:
        await self.get_credential(user, credential_id)
        result = await self.db.execute(
            select(AmazonSyncState).where(AmazonSyncState.credential_id == credential_id)
        )
        return list(result.scalars().all())

    # --- Orders / sales -------------------------------------------------------------

    async def list_orders(
        self,
        user: User,
        credential_id: uuid.UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AmazonOrder], int]:
        await self.get_credential(user, credential_id)
        query = select(AmazonOrder).where(AmazonOrder.credential_id == credential_id)
        if start is not None:
            query = query.where(AmazonOrder.purchase_date >= start)
        if end is not None:
            query = query.where(AmazonOrder.purchase_date <= end)

        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.options(selectinload(AmazonOrder.items))
            .order_by(AmazonOrder.purchase_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def sales_summary(
        self, user: User, credential_id: uuid.UUID, *, start: datetime, end: datetime
    ) -> dict:
        await self.get_credential(user, credential_id)
        result = await self.db.execute(
            select(
                func.count(AmazonOrder.id),
                func.coalesce(func.sum(AmazonOrder.order_total_amount), 0),
                func.max(AmazonOrder.order_total_currency),
            ).where(
                AmazonOrder.credential_id == credential_id,
                AmazonOrder.purchase_date >= start,
                AmazonOrder.purchase_date <= end,
                AmazonOrder.order_status != "Cancelled",
            )
        )
        order_count, total_revenue, currency = result.one()
        total_revenue = Decimal(total_revenue)
        avg = (total_revenue / order_count) if order_count else Decimal("0")
        return {
            "start": start,
            "end": end,
            "order_count": order_count,
            "total_revenue": total_revenue,
            "currency": currency,
            "average_order_value": avg,
        }

    # --- Listings ------------------------------------------------------------------

    async def list_listings(
        self,
        user: User,
        credential_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AmazonListing], int]:
        await self.get_credential(user, credential_id)
        query = select(AmazonListing).where(AmazonListing.credential_id == credential_id)
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(AmazonListing.seller_sku).offset((page - 1) * page_size).limit(page_size)
        )
        return list(result.scalars().all()), total

    # --- Inventory ------------------------------------------------------------------

    async def latest_inventory(
        self, user: User, credential_id: uuid.UUID, *, low_stock_only: bool = False
    ) -> list[AmazonInventorySnapshot]:
        await self.get_credential(user, credential_id)
        latest_per_sku = (
            select(
                AmazonInventorySnapshot.seller_sku,
                func.max(AmazonInventorySnapshot.snapshot_at).label("max_snapshot_at"),
            )
            .where(AmazonInventorySnapshot.credential_id == credential_id)
            .group_by(AmazonInventorySnapshot.seller_sku)
            .subquery()
        )
        query = select(AmazonInventorySnapshot).join(
            latest_per_sku,
            (AmazonInventorySnapshot.seller_sku == latest_per_sku.c.seller_sku)
            & (AmazonInventorySnapshot.snapshot_at == latest_per_sku.c.max_snapshot_at),
        )
        if low_stock_only:
            query = query.where(AmazonInventorySnapshot.fulfillable_quantity < LOW_STOCK_THRESHOLD)
        result = await self.db.execute(
            query.where(AmazonInventorySnapshot.credential_id == credential_id).order_by(
                AmazonInventorySnapshot.seller_sku
            )
        )
        return list(result.scalars().all())

    # --- FBA shipments -----------------------------------------------------------

    async def list_fba_shipments(
        self, user: User, credential_id: uuid.UUID, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[AmazonFbaShipment], int]:
        await self.get_credential(user, credential_id)
        query = select(AmazonFbaShipment).where(AmazonFbaShipment.credential_id == credential_id)
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(AmazonFbaShipment.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def open_shipment_count(self, credential_id: uuid.UUID) -> int:
        closed_statuses = ("CLOSED", "CANCELLED", "DELETED")
        result = await self.db.execute(
            select(func.count()).where(
                AmazonFbaShipment.credential_id == credential_id,
                AmazonFbaShipment.shipment_status.notin_(closed_statuses),
            )
        )
        return result.scalar_one()

    # --- Financial events -----------------------------------------------------------

    async def list_financial_events(
        self,
        user: User,
        credential_id: uuid.UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AmazonFinancialEvent], int]:
        await self.get_credential(user, credential_id)
        query = select(AmazonFinancialEvent).where(
            AmazonFinancialEvent.credential_id == credential_id
        )
        if start is not None:
            query = query.where(AmazonFinancialEvent.posted_date >= start)
        if end is not None:
            query = query.where(AmazonFinancialEvent.posted_date <= end)
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(AmazonFinancialEvent.posted_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def financial_summary(
        self, user: User, credential_id: uuid.UUID, *, start: datetime, end: datetime
    ) -> dict:
        await self.get_credential(user, credential_id)
        result = await self.db.execute(
            select(
                AmazonFinancialEvent.event_type,
                func.coalesce(func.sum(AmazonFinancialEvent.amount), 0),
                func.max(AmazonFinancialEvent.currency),
            )
            .where(
                AmazonFinancialEvent.credential_id == credential_id,
                AmazonFinancialEvent.posted_date >= start,
                AmazonFinancialEvent.posted_date <= end,
            )
            .group_by(AmazonFinancialEvent.event_type)
        )
        by_type: dict[str, Decimal] = {}
        net_amount = Decimal("0")
        currency: str | None = None
        for event_type, total, evt_currency in result.all():
            total = Decimal(total)
            by_type[event_type] = total
            net_amount += total
            currency = currency or evt_currency
        return {
            "start": start,
            "end": end,
            "net_amount": net_amount,
            "currency": currency,
            "by_type": by_type,
        }

    # --- Dashboard -------------------------------------------------------------------

    async def dashboard(self, user: User, credential_id: uuid.UUID, *, days: int = 30) -> dict:
        await self.get_credential(user, credential_id)
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        sales = await self.sales_summary(user, credential_id, start=start, end=end)
        financials = await self.financial_summary(user, credential_id, start=start, end=end)
        low_stock = await self.latest_inventory(user, credential_id, low_stock_only=True)
        open_shipments = await self.open_shipment_count(credential_id)
        return {
            "sales": sales,
            "financials": financials,
            "low_stock": low_stock,
            "open_shipments": open_shipments,
            "extra": {},
        }
