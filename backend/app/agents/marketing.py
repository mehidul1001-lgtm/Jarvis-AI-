"""Marketing Agent: SEO, listings, A+ content, advertising guidance."""

from __future__ import annotations

import json
import re
from collections import Counter

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "of",
    "to",
    "in",
    "on",
    "is",
    "are",
    "your",
    "our",
    "this",
    "that",
    "it",
    "by",
    "from",
    "at",
    "as",
    "be",
}


class MarketingAgent(BaseAgent):
    name = "marketing"
    display_name = "Marketing Agent"
    description = "SEO, Amazon listings, A+ content and advertising recommendations."
    persona = (
        "You are the marketing specialist. You write and optimize Amazon listings "
        "(title, bullets, description, backend keywords), A+ content outlines and "
        "ad copy, and you advise on campaign structure. Use listing_quality_check "
        "on any listing you produce or review, and keep brand voice rules stored "
        "in memory ('business_rule') in mind."
    )

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="listing_quality_check",
            description=(
                "Analyze an Amazon listing draft: title length, bullet coverage, "
                "keyword usage and duplication. Pass the full draft text."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "target_keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "bullets", "target_keywords"],
                "additionalProperties": False,
            },
        )
        async def listing_quality_check(
            ctx: ToolContext, title: str, bullets: list[str], target_keywords: list[str]
        ) -> str:
            issues: list[str] = []
            if len(title) > 200:
                issues.append(f"Title is {len(title)} chars; Amazon truncates at ~200.")
            if len(title) < 80:
                issues.append("Title under 80 chars — likely wasting keyword space.")
            if not 4 <= len(bullets) <= 6:
                issues.append(f"{len(bullets)} bullets; aim for 5.")
            for index, bullet in enumerate(bullets):
                if len(bullet) > 500:
                    issues.append(f"Bullet {index + 1} exceeds 500 chars.")

            corpus = (title + " " + " ".join(bullets)).lower()
            missing = [kw for kw in target_keywords if kw.lower() not in corpus]
            words = [word for word in re.findall(r"[a-z0-9']+", corpus) if word not in STOPWORDS]
            top = Counter(words).most_common(5)
            overused = [word for word, count in top if count >= 6]
            if overused:
                issues.append(f"Possible keyword stuffing: {', '.join(overused)}")

            return json.dumps(
                {
                    "issues": issues,
                    "missing_keywords": missing,
                    "keyword_coverage_pct": round(
                        (len(target_keywords) - len(missing)) / max(1, len(target_keywords)) * 100
                    ),
                    "top_terms": [{"term": word, "count": count} for word, count in top],
                }
            )


AGENT = MarketingAgent
