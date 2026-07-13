"""Amazon Agent: inventory, PPC, listings, keywords, profitability."""

from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry


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
        "Direct Amazon account integration (SP-API) is not connected yet; when live "
        "account data is required, say so explicitly and work from stored data."
    )

    def register_domain_tools(self, registry: ToolRegistry) -> None:
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
