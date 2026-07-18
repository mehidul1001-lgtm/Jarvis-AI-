"""Schemas for connected Amazon accounts and synced business data."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.amazon import AmazonRegion

# --- Credentials -------------------------------------------------------------


class AmazonCredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    region: AmazonRegion
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_id: str = Field(min_length=1, max_length=50)
    lwa_client_id: str = Field(min_length=1, max_length=200)
    lwa_client_secret: str = Field(min_length=1)
    lwa_refresh_token: str = Field(min_length=1)


class AmazonCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    region: AmazonRegion
    marketplace_id: str
    seller_id: str
    lwa_client_id: str
    is_active: bool
    created_at: datetime


class AmazonSyncStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource: str
    status: str
    last_synced_at: datetime | None
    last_error: str | None
    records_last_sync: int


class SyncTriggerRequest(BaseModel):
    resource: str = Field(
        default="all",
        pattern="^(all|orders|listings|inventory|fba_shipments|financial_events)$",
    )
    recurring: bool = False


class SyncTriggerOut(BaseModel):
    task_id: uuid.UUID
    resource: str


# --- Orders -------------------------------------------------------------------


class AmazonOrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asin: str | None
    seller_sku: str | None
    title: str | None
    quantity_ordered: int
    quantity_shipped: int
    item_price_amount: Decimal | None
    item_price_currency: str | None


class AmazonOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amazon_order_id: str
    purchase_date: datetime
    order_status: str
    fulfillment_channel: str | None
    marketplace_id: str
    order_total_amount: Decimal | None
    order_total_currency: str | None
    items: list[AmazonOrderItemOut] = []


class SalesSummaryOut(BaseModel):
    start: datetime
    end: datetime
    order_count: int
    total_revenue: Decimal
    currency: str | None
    average_order_value: Decimal


# --- Listings -----------------------------------------------------------------


class AmazonListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seller_sku: str
    asin: str | None
    product_type: str | None
    item_name: str | None
    condition_type: str | None
    status: list[str] | None
    main_image_url: str | None
    listing_updated_at: datetime | None


# --- Inventory ----------------------------------------------------------------


class InventorySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seller_sku: str
    asin: str | None
    condition: str | None
    total_quantity: int
    fulfillable_quantity: int
    inbound_working_quantity: int
    inbound_shipped_quantity: int
    reserved_quantity: int
    unfulfillable_quantity: int
    snapshot_at: datetime


# --- FBA shipments --------------------------------------------------------------


class FbaShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    shipment_id: str
    shipment_name: str | None
    destination_fulfillment_center_id: str | None
    shipment_status: str
    updated_at: datetime


# --- Financial events -----------------------------------------------------------


class FinancialEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    amazon_order_id: str | None
    posted_date: datetime
    amount: Decimal
    currency: str | None
    description: str | None


class FinancialSummaryOut(BaseModel):
    start: datetime
    end: datetime
    net_amount: Decimal
    currency: str | None
    by_type: dict[str, Decimal]


class AmazonDashboardOut(BaseModel):
    """Aggregated snapshot for the enterprise dashboard's Amazon panel."""

    sales: SalesSummaryOut
    financials: FinancialSummaryOut
    low_stock: list[InventorySnapshotOut]
    open_shipments: int
    extra: dict[str, Any] = {}
