"""Amazon Selling Partner API integration: credentials, sync state and
synced business data (orders, inventory, FBA shipments, financial events).

Amazon retired the AWS SigV4/IAM-role signing requirement for SP-API in its
2023 migration; every operation used here only needs a Login-with-Amazon
(LWA) access token, so credentials only need LWA fields — see
``app/integrations/amazon/auth.py``.

Order records intentionally exclude buyer PII (name, address, email).
Amazon gates that data behind a separate Restricted Data Token flow this
integration does not implement; only order/financial/inventory data needed
for business analytics is stored.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AmazonRegion(str, enum.Enum):
    NA = "NA"
    EU = "EU"
    FE = "FE"


class SyncStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class AmazonCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One connected Amazon seller account (Login-with-Amazon refresh token)."""

    __tablename__ = "amazon_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[AmazonRegion] = mapped_column(
        Enum(AmazonRegion, name="amazon_region", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_id: Mapped[str] = mapped_column(String(50), nullable=False)
    lwa_client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # Symmetrically encrypted at rest (app.core.security.encrypt_value/decrypt_value).
    lwa_client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    lwa_refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_states: Mapped[list[AmazonSyncState]] = relationship(
        back_populates="credential", cascade="all, delete-orphan"
    )


class AmazonSyncState(Base, UUIDPrimaryKeyMixin):
    """Per-resource sync cursor so repeated syncs are incremental, not full re-pulls."""

    __tablename__ = "amazon_sync_states"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("amazon_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    # Amazon "NextToken" for an in-progress paginated pull, if any.
    next_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Watermark for the next sync's "created/updated/posted after" filter.
    cursor_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="amazon_sync_status", values_callable=lambda e: [m.value for m in e]),
        default=SyncStatus.IDLE,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_last_sync: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    credential: Mapped[AmazonCredential] = relationship(back_populates="sync_states")

    __table_args__ = (
        UniqueConstraint("credential_id", "resource", name="uq_amazon_sync_resource"),
    )


class AmazonOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "amazon_orders"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_credentials.id", ondelete="CASCADE"), nullable=False
    )
    amazon_order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    purchase_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_update_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_status: Mapped[str] = mapped_column(String(30), nullable=False)
    fulfillment_channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sales_channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    number_of_items_shipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    number_of_items_unshipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    order_total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    order_total_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_business_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    items: Mapped[list[AmazonOrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_order_id", name="uq_amazon_order"),
        Index("ix_amazon_orders_credential_purchase_date", "credential_id", "purchase_date"),
    )


class AmazonOrderItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "amazon_order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_orders.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[str] = mapped_column(String(50), nullable=False)
    asin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    seller_sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity_ordered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity_shipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    item_price_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    item_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    promotion_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    order: Mapped[AmazonOrder] = relationship(back_populates="items")

    __table_args__ = (UniqueConstraint("order_id", "order_item_id", name="uq_amazon_order_item"),)


class AmazonInventorySnapshot(Base, UUIDPrimaryKeyMixin):
    """Append-only point-in-time FBA inventory reading (powers trend/heat-map views)."""

    __tablename__ = "amazon_inventory_snapshots"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_credentials.id", ondelete="CASCADE"), nullable=False
    )
    asin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fnsku: Mapped[str | None] = mapped_column(String(20), nullable=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    condition: Mapped[str | None] = mapped_column(String(30), nullable=True)
    total_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fulfillable_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inbound_working_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inbound_shipped_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inbound_receiving_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unfulfillable_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_amazon_inventory_credential_sku_time",
            "credential_id",
            "seller_sku",
            "snapshot_at",
        ),
    )


class AmazonFbaShipment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "amazon_fba_shipments"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_credentials.id", ondelete="CASCADE"), nullable=False
    )
    shipment_id: Mapped[str] = mapped_column(String(50), nullable=False)
    shipment_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    destination_fulfillment_center_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shipment_status: Mapped[str] = mapped_column(String(30), nullable=False)
    label_prep_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    __table_args__ = (
        UniqueConstraint("credential_id", "shipment_id", name="uq_amazon_fba_shipment"),
    )


class AmazonListing(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One marketplace listing (SKU) from the Listings Items API.

    Upserted on every sync; a listing removed on Amazon keeps its last-seen
    row here (its ``status`` reflects the final state Amazon reported).
    """

    __tablename__ = "amazon_listings"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_credentials.id", ondelete="CASCADE"), nullable=False
    )
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    asin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    condition_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Amazon reports zero or more states, e.g. ["BUYABLE", "DISCOVERABLE"].
    status: Mapped[list[str] | None] = mapped_column(ARRAY(String(30)), nullable=True)
    main_image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    listing_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    listing_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("credential_id", "seller_sku", name="uq_amazon_listing"),)


class AmazonFinancialEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "amazon_financial_events"

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amazon_credentials.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amazon_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    posted_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Full original event payload; event shapes vary widely by type.
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Stable hash of the identifying fields; de-duplicates re-synced overlap windows.
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("credential_id", "dedupe_key", name="uq_amazon_financial_event"),
        Index("ix_amazon_financial_events_credential_posted", "credential_id", "posted_date"),
    )
