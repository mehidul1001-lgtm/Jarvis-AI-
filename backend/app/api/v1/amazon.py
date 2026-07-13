"""Amazon SP-API integration: connected accounts and synced business data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, ManagerUser
from app.core.exceptions import ValidationFailedError
from app.schemas.amazon import (
    AmazonCredentialCreate,
    AmazonCredentialOut,
    AmazonDashboardOut,
    AmazonOrderOut,
    AmazonSyncStateOut,
    FbaShipmentOut,
    FinancialEventOut,
    FinancialSummaryOut,
    InventorySnapshotOut,
    SalesSummaryOut,
    SyncTriggerOut,
    SyncTriggerRequest,
)
from app.schemas.common import Message as MessageEnvelope
from app.schemas.common import Page
from app.services.amazon_data_service import AmazonDataService

router = APIRouter(prefix="/amazon", tags=["amazon"])


def _date_range(
    start: datetime | None, end: datetime | None, *, default_days: int = 30
) -> tuple[datetime, datetime]:
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=default_days)
    if start > end:
        raise ValidationFailedError("start must be before end")
    return start, end


# --- Credentials ---------------------------------------------------------------


@router.post(
    "/credentials", response_model=AmazonCredentialOut, status_code=status.HTTP_201_CREATED
)
async def connect_account(
    payload: AmazonCredentialCreate, user: ManagerUser, db: DbSession
) -> AmazonCredentialOut:
    credential = await AmazonDataService(db).create_credential(user, payload)
    return AmazonCredentialOut.model_validate(credential)


@router.get("/credentials", response_model=list[AmazonCredentialOut])
async def list_accounts(user: CurrentUser, db: DbSession) -> list[AmazonCredentialOut]:
    credentials = await AmazonDataService(db).list_credentials(user)
    return [AmazonCredentialOut.model_validate(c) for c in credentials]


@router.delete("/credentials/{credential_id}", response_model=MessageEnvelope)
async def disconnect_account(
    credential_id: uuid.UUID, user: ManagerUser, db: DbSession
) -> MessageEnvelope:
    await AmazonDataService(db).delete_credential(user, credential_id)
    return MessageEnvelope(message="Amazon account disconnected")


@router.get("/credentials/{credential_id}/sync-state", response_model=list[AmazonSyncStateOut])
async def sync_state(
    credential_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[AmazonSyncStateOut]:
    states = await AmazonDataService(db).sync_states(user, credential_id)
    return [AmazonSyncStateOut.model_validate(s) for s in states]


@router.post("/credentials/{credential_id}/sync", response_model=SyncTriggerOut)
async def trigger_sync(
    credential_id: uuid.UUID, payload: SyncTriggerRequest, user: ManagerUser, db: DbSession
) -> SyncTriggerOut:
    from app.services.task_service import TaskService

    # Confirms ownership before enqueueing so a task can't be created for
    # a credential the caller doesn't own.
    await AmazonDataService(db).get_credential(user, credential_id)
    task = await TaskService(db).create(
        user,
        title=f"Amazon sync ({payload.resource})",
        handler="amazon.sync",
        payload={
            "credential_id": str(credential_id),
            "resource": payload.resource,
            "recurring": payload.recurring,
        },
    )
    return SyncTriggerOut(task_id=task.id, resource=payload.resource)


# --- Orders / sales -------------------------------------------------------------


@router.get("/credentials/{credential_id}/orders", response_model=Page[AmazonOrderOut])
async def list_orders(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AmazonOrderOut]:
    range_start, range_end = _date_range(start, end, default_days=90)
    orders, total = await AmazonDataService(db).list_orders(
        user, credential_id, start=range_start, end=range_end, page=page, page_size=page_size
    )
    return Page(
        items=[AmazonOrderOut.model_validate(o) for o in orders],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/credentials/{credential_id}/orders/summary", response_model=SalesSummaryOut)
async def sales_summary(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> SalesSummaryOut:
    range_start, range_end = _date_range(start, end)
    summary = await AmazonDataService(db).sales_summary(
        user, credential_id, start=range_start, end=range_end
    )
    return SalesSummaryOut(**summary)


# --- Inventory ------------------------------------------------------------------


@router.get("/credentials/{credential_id}/inventory", response_model=list[InventorySnapshotOut])
async def inventory(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    low_stock_only: bool = Query(default=False),
) -> list[InventorySnapshotOut]:
    snapshots = await AmazonDataService(db).latest_inventory(
        user, credential_id, low_stock_only=low_stock_only
    )
    return [InventorySnapshotOut.model_validate(s) for s in snapshots]


# --- FBA shipments -----------------------------------------------------------


@router.get("/credentials/{credential_id}/fba-shipments", response_model=Page[FbaShipmentOut])
async def fba_shipments(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[FbaShipmentOut]:
    shipments, total = await AmazonDataService(db).list_fba_shipments(
        user, credential_id, page=page, page_size=page_size
    )
    return Page(
        items=[FbaShipmentOut.model_validate(s) for s in shipments],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- Financial events -----------------------------------------------------------


@router.get("/credentials/{credential_id}/financial-events", response_model=Page[FinancialEventOut])
async def financial_events(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[FinancialEventOut]:
    range_start, range_end = _date_range(start, end, default_days=90)
    events, total = await AmazonDataService(db).list_financial_events(
        user, credential_id, start=range_start, end=range_end, page=page, page_size=page_size
    )
    return Page(
        items=[FinancialEventOut.model_validate(e) for e in events],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/credentials/{credential_id}/financial-events/summary", response_model=FinancialSummaryOut
)
async def financial_summary(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> FinancialSummaryOut:
    range_start, range_end = _date_range(start, end)
    summary = await AmazonDataService(db).financial_summary(
        user, credential_id, start=range_start, end=range_end
    )
    return FinancialSummaryOut(**summary)


# --- Dashboard -------------------------------------------------------------------


@router.get("/credentials/{credential_id}/dashboard", response_model=AmazonDashboardOut)
async def dashboard(
    credential_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> AmazonDashboardOut:
    data = await AmazonDataService(db).dashboard(user, credential_id, days=days)
    return AmazonDashboardOut(**data)
