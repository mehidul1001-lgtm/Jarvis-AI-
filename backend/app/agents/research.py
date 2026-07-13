"""Product Research Agent: sourcing, competition, IP risk, opportunity scoring."""

from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry


class ResearchAgent(BaseAgent):
    name = "research"
    display_name = "Product Research Agent"
    description = "Supplier analysis, profitability, competition and IP-risk workflows."
    persona = (
        "You are the product research specialist. You evaluate product opportunities: "
        "demand vs competition, unit economics, supplier quality and IP/brand risk. "
        "Store promising products and vetted suppliers to memory (kinds 'product' and "
        "'supplier'). Always run the opportunity scorer on candidate products so "
        "evaluations are consistent. For IP risk, walk the standard checklist: "
        "trademark search, patent exposure, brand-gating, and certification "
        "requirements — and record the outcome as a 'decision' memory. Live "
        "marketplace data feeds are not connected yet; when live data is required, "
        "say so and reason from figures the user provides."
    )

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="score_opportunity",
            description=(
                "Score a product opportunity 0-100 from demand, competition and "
                "economics inputs. Consistent rubric across all evaluations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "monthly_demand_units": {"type": "number"},
                    "competitor_count": {"type": "number"},
                    "avg_competitor_reviews": {"type": "number"},
                    "margin_pct": {"type": "number", "description": "Projected net margin %"},
                    "startup_cost": {
                        "type": "number",
                        "description": "Initial order + launch cost",
                    },
                    "differentiation": {
                        "type": "string",
                        "enum": ["none", "minor", "strong"],
                    },
                },
                "required": [
                    "monthly_demand_units",
                    "competitor_count",
                    "avg_competitor_reviews",
                    "margin_pct",
                    "startup_cost",
                    "differentiation",
                ],
                "additionalProperties": False,
            },
        )
        async def score_opportunity(
            ctx: ToolContext,
            monthly_demand_units: float,
            competitor_count: float,
            avg_competitor_reviews: float,
            margin_pct: float,
            startup_cost: float,
            differentiation: str,
        ) -> str:
            demand = min(25.0, monthly_demand_units / 40)
            competition = max(0.0, 25 - competitor_count * 1.2 - avg_competitor_reviews / 80)
            economics = max(0.0, min(30.0, margin_pct))
            capital = max(0.0, 10 - startup_cost / 2000)
            diff = {"none": 0, "minor": 5, "strong": 10}[differentiation]
            score = round(demand + competition + economics + capital + diff, 1)
            verdict = (
                "strong candidate"
                if score >= 65
                else "worth deeper research"
                if score >= 45
                else "weak opportunity"
            )
            return json.dumps(
                {
                    "score": score,
                    "verdict": verdict,
                    "components": {
                        "demand": round(demand, 1),
                        "competition": round(competition, 1),
                        "economics": round(economics, 1),
                        "capital_efficiency": round(capital, 1),
                        "differentiation": diff,
                    },
                }
            )


AGENT = ResearchAgent
