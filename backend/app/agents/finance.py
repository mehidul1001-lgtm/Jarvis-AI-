"""Finance Agent: expenses, income, P&L, cash flow."""

from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry


class FinanceAgent(BaseAgent):
    name = "finance"
    display_name = "Finance Agent"
    description = "Expenses, income, profit & loss, cash flow and financial planning."
    persona = (
        "You are the finance specialist. You track expenses and income, produce "
        "profit-and-loss views, and model cash flow. Financial facts the user shares "
        "(recurring costs, revenue figures, margins, obligations) should be saved to "
        "memory so they persist. Use the calculation tools for projections — never "
        "invent numbers. Bank feeds are not connected yet; when live balances are "
        "required, say so and work from stored figures."
    )

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="profit_and_loss",
            description=(
                "Compute a simple P&L from revenue and cost line items. Pass items as "
                "arrays of {label, amount} objects."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "revenue_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "amount": {"type": "number"},
                            },
                            "required": ["label", "amount"],
                            "additionalProperties": False,
                        },
                    },
                    "cost_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "amount": {"type": "number"},
                            },
                            "required": ["label", "amount"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["revenue_items", "cost_items"],
                "additionalProperties": False,
            },
        )
        async def profit_and_loss(
            ctx: ToolContext, revenue_items: list[dict], cost_items: list[dict]
        ) -> str:
            revenue = round(sum(float(item["amount"]) for item in revenue_items), 2)
            costs = round(sum(float(item["amount"]) for item in cost_items), 2)
            net = round(revenue - costs, 2)
            return json.dumps(
                {
                    "total_revenue": revenue,
                    "total_costs": costs,
                    "net_profit": net,
                    "net_margin_pct": round(net / revenue * 100, 1) if revenue else None,
                }
            )

        @registry.tool(
            name="cashflow_projection",
            description=(
                "Project month-by-month cash position from a starting balance, "
                "monthly inflow, monthly outflow and optional growth rates."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "starting_balance": {"type": "number"},
                    "monthly_inflow": {"type": "number"},
                    "monthly_outflow": {"type": "number"},
                    "months": {"type": ["integer", "null"], "description": "Default 6, max 24"},
                    "inflow_growth_pct": {"type": ["number", "null"]},
                    "outflow_growth_pct": {"type": ["number", "null"]},
                },
                "required": ["starting_balance", "monthly_inflow", "monthly_outflow"],
                "additionalProperties": False,
            },
        )
        async def cashflow_projection(
            ctx: ToolContext,
            starting_balance: float,
            monthly_inflow: float,
            monthly_outflow: float,
            months: int | None = None,
            inflow_growth_pct: float | None = None,
            outflow_growth_pct: float | None = None,
        ) -> str:
            horizon = min(max(months or 6, 1), 24)
            inflow, outflow, balance = monthly_inflow, monthly_outflow, starting_balance
            series = []
            negative_month = None
            for month in range(1, horizon + 1):
                balance = round(balance + inflow - outflow, 2)
                series.append(
                    {
                        "month": month,
                        "inflow": round(inflow, 2),
                        "outflow": round(outflow, 2),
                        "balance": balance,
                    }
                )
                if negative_month is None and balance < 0:
                    negative_month = month
                inflow *= 1 + (inflow_growth_pct or 0) / 100
                outflow *= 1 + (outflow_growth_pct or 0) / 100
            return json.dumps({"projection": series, "first_negative_month": negative_month})


AGENT = FinanceAgent
