"""Amazon Agent: inventory, PPC, listings, keywords, profitability."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry
from app.models.amazon import AmazonCredential
from app.services.amazon_data_service import AmazonDataService


class AmazonAgent(BaseAgent):
    name = "amazon"
    display_name = "Amazon Agent"
    description = "Inventory, PPC, listings, keyword analysis, profitability and reports."
    persona = (
        "You are the Amazon operations specialist. You manage the user's Amazon "
        "business: inventory planning, PPC campaign analysis, listing optimization, "
        "keyword strategy and unit economics. Products and suppliers live in memory "
        "(kinds 'product' and 'supplier') — search them before answering. Use the "
        "calculation tools for fees and PPC math instead of estimating in your head. "
        "If a Seller Central account is connected (check with amazon_list_accounts), "
        "use amazon_sales_summary/amazon_inventory_status/amazon_recent_orders to "
        "ground answers in real synced data instead of guessing; if none is "
        "connected, say so explicitly and fall back to memory-stored figures."
    )

    async def _resolve_credential(
        self, ctx: ToolContext, credential_id: str | None
    ) -> AmazonCredential | str:
        """Returns the credential to use, or an error string for the model to relay."""
        service = AmazonDataService(ctx.db)
        if credential_id:
            try:
                return await service.get_credential(ctx.user, uuid.UUID(credential_id))
            except (ValueError, LookupError):
                return f"Error: no connected Amazon account with id '{credential_id}'."
        accounts = await service.list_credentials(ctx.user)
        if not accounts:
            return "No Amazon Seller Central account is connected yet."
        if len(accounts) > 1:
            labels = ", ".join(f"{a.label} ({a.id})" for a in accounts)
            return f"Multiple Amazon accounts are connected; specify credential_id: {labels}"
        return accounts[0]

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="amazon_list_accounts",
            description="List the Amazon Seller Central accounts connected to this business.",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        async def amazon_list_accounts(ctx: ToolContext) -> str:
            accounts = await AmazonDataService(ctx.db).list_credentials(ctx.user)
            if not accounts:
                return "No Amazon account is connected."
            return json.dumps(
                [
                    {
                        "id": str(a.id),
                        "label": a.label,
                        "region": a.region.value,
                        "marketplace_id": a.marketplace_id,
                        "is_active": a.is_active,
                    }
                    for a in accounts
                ]
            )

        @registry.tool(
            name="amazon_sales_summary",
            description=(
                "Real sales revenue/order-count/AOV for a connected Amazon account over the "
                "last N days, from synced order data."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": ["number", "null"],
                        "description": "Lookback window, default 30",
                    },
                    "credential_id": {
                        "type": ["string", "null"],
                        "description": "Which connected account, if more than one",
                    },
                },
                "required": [],
            },
        )
        async def amazon_sales_summary(
            ctx: ToolContext, days: float | None = None, credential_id: str | None = None
        ) -> str:
            credential = await self._resolve_credential(ctx, credential_id)
            if isinstance(credential, str):
                return credential
            end = datetime.now(UTC)
            start = end - timedelta(days=int(days) if days else 30)
            summary = await AmazonDataService(ctx.db).sales_summary(
                ctx.user, credential.id, start=start, end=end
            )
            return json.dumps(summary, default=str)

        @registry.tool(
            name="amazon_inventory_status",
            description=(
                "Real FBA inventory levels (fulfillable/inbound/reserved quantities) per SKU "
                "from the most recent sync, optionally filtered to low-stock SKUs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "low_stock_only": {"type": ["boolean", "null"]},
                    "credential_id": {"type": ["string", "null"]},
                },
                "required": [],
            },
        )
        async def amazon_inventory_status(
            ctx: ToolContext, low_stock_only: bool | None = None, credential_id: str | None = None
        ) -> str:
            credential = await self._resolve_credential(ctx, credential_id)
            if isinstance(credential, str):
                return credential
            snapshots = await AmazonDataService(ctx.db).latest_inventory(
                ctx.user, credential.id, low_stock_only=bool(low_stock_only)
            )
            if not snapshots:
                return "No inventory data yet — run an Amazon sync first."
            return json.dumps(
                [
                    {
                        "seller_sku": s.seller_sku,
                        "asin": s.asin,
                        "fulfillable_quantity": s.fulfillable_quantity,
                        "inbound_shipped_quantity": s.inbound_shipped_quantity,
                        "reserved_quantity": s.reserved_quantity,
                        "as_of": s.snapshot_at.isoformat(),
                    }
                    for s in snapshots[:50]
                ]
            )

        @registry.tool(
            name="amazon_recent_orders",
            description="Most recent real Amazon orders from synced data.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": ["number", "null"], "description": "Default 10, max 50"},
                    "credential_id": {"type": ["string", "null"]},
                },
                "required": [],
            },
        )
        async def amazon_recent_orders(
            ctx: ToolContext, limit: float | None = None, credential_id: str | None = None
        ) -> str:
            credential = await self._resolve_credential(ctx, credential_id)
            if isinstance(credential, str):
                return credential
            page_size = min(int(limit) if limit else 10, 50)
            orders, total = await AmazonDataService(ctx.db).list_orders(
                ctx.user, credential.id, page=1, page_size=page_size
            )
            if not orders:
                return "No orders synced yet — run an Amazon sync first."
            return json.dumps(
                {
                    "total_in_range": total,
                    "orders": [
                        {
                            "amazon_order_id": o.amazon_order_id,
                            "purchase_date": o.purchase_date.isoformat(),
                            "status": o.order_status,
                            "total": str(o.order_total_amount) if o.order_total_amount else None,
                            "currency": o.order_total_currency,
                        }
                        for o in orders
                    ],
                }
            )

        @registry.tool(
            name="fba_profitability",
            description=(
                "Compute FBA unit economics: referral fee, margin, ROI and break-even "
                "ACOS from price, landed cost and FBA fulfillment fee."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "sale_price": {
                        "type": "number",
                        "description": "Selling price in your currency",
                    },
                    "landed_cost": {
                        "type": "number",
                        "description": "Product cost incl. shipping/duties",
                    },
                    "fba_fee": {"type": "number", "description": "FBA fulfillment fee per unit"},
                    "referral_fee_pct": {
                        "type": ["number", "null"],
                        "description": "Referral fee percent, default 15",
                    },
                    "monthly_units": {"type": ["number", "null"], "description": "Optional volume"},
                },
                "required": ["sale_price", "landed_cost", "fba_fee"],
                "additionalProperties": False,
            },
        )
        async def fba_profitability(
            ctx: ToolContext,
            sale_price: float,
            landed_cost: float,
            fba_fee: float,
            referral_fee_pct: float | None = None,
            monthly_units: float | None = None,
        ) -> str:
            if sale_price <= 0:
                return "Error: sale_price must be positive."
            referral_pct = (referral_fee_pct if referral_fee_pct is not None else 15.0) / 100.0
            referral_fee = round(sale_price * referral_pct, 2)
            profit = round(sale_price - landed_cost - fba_fee - referral_fee, 2)
            margin = round(profit / sale_price * 100, 1)
            roi = round(profit / landed_cost * 100, 1) if landed_cost > 0 else None
            breakeven_acos = round(profit / sale_price * 100, 1)
            result = {
                "referral_fee": referral_fee,
                "net_profit_per_unit": profit,
                "margin_pct": margin,
                "roi_pct": roi,
                "breakeven_acos_pct": breakeven_acos,
            }
            if monthly_units:
                result["monthly_profit"] = round(profit * monthly_units, 2)
            return json.dumps(result)

        @registry.tool(
            name="ppc_metrics",
            description=(
                "Compute PPC performance metrics (ACOS, TACOS, CTR, CVR, CPC, "
                "suggested bid direction) from campaign numbers."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ad_spend": {"type": "number"},
                    "ad_sales": {"type": "number"},
                    "total_sales": {"type": ["number", "null"]},
                    "impressions": {"type": ["number", "null"]},
                    "clicks": {"type": ["number", "null"]},
                    "orders": {"type": ["number", "null"]},
                    "target_acos_pct": {"type": ["number", "null"]},
                },
                "required": ["ad_spend", "ad_sales"],
                "additionalProperties": False,
            },
        )
        async def ppc_metrics(
            ctx: ToolContext,
            ad_spend: float,
            ad_sales: float,
            total_sales: float | None = None,
            impressions: float | None = None,
            clicks: float | None = None,
            orders: float | None = None,
            target_acos_pct: float | None = None,
        ) -> str:
            metrics: dict = {}
            metrics["acos_pct"] = round(ad_spend / ad_sales * 100, 1) if ad_sales > 0 else None
            if total_sales and total_sales > 0:
                metrics["tacos_pct"] = round(ad_spend / total_sales * 100, 1)
            if impressions and clicks is not None and impressions > 0:
                metrics["ctr_pct"] = round(clicks / impressions * 100, 2)
            if clicks and clicks > 0:
                metrics["cpc"] = round(ad_spend / clicks, 2)
                if orders is not None:
                    metrics["cvr_pct"] = round(orders / clicks * 100, 1)
            if target_acos_pct and metrics.get("acos_pct") is not None:
                if metrics["acos_pct"] > target_acos_pct * 1.1:
                    metrics["bid_suggestion"] = "reduce bids ~10-20% or trim poor search terms"
                elif metrics["acos_pct"] < target_acos_pct * 0.8:
                    metrics["bid_suggestion"] = "headroom to raise bids for volume"
                else:
                    metrics["bid_suggestion"] = "on target; hold bids"
            return json.dumps(metrics)


AGENT = AmazonAgent
