"""Planning engine: objective decomposition and execution planning.

Produces validated, dependency-ordered step plans. Agents plan before they
execute (see ``BaseAgent._plan``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.ai.llm import LLMClient, LLMRequest, get_llm_client
from app.core.config import get_settings

logger = logging.getLogger("jarvis.ai.planner")

PLANNER_PROMPT = """\
You are a planning engine. Decompose the objective into the smallest set of
concrete, executable steps (1-8 steps). Respond with JSON only:
{"steps": [{"title": "...", "description": "...", "depends_on": [0-based step indexes]}]}
Rules:
- Steps must be actionable and verifiable, not vague.
- depends_on may only reference earlier steps.
- Independent steps should not declare dependencies (they may run in parallel).
- Prefer fewer steps; a trivial objective is a single step."""


@dataclass
class PlanStep:
    title: str
    description: str
    depends_on: list[int] = field(default_factory=list)


@dataclass
class Plan:
    objective: str
    steps: list[PlanStep]


class PlanningEngine:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm_client()
        self.settings = get_settings()

    async def decompose(self, objective: str, context: str | None = None) -> Plan:
        content = objective if not context else f"{objective}\n\nContext:\n{context}"
        response = await self.llm.generate(
            LLMRequest(
                system=[{"type": "text", "text": PLANNER_PROMPT}],
                messages=[{"role": "user", "content": content[:12000]}],
                max_tokens=2000,
                model=self.settings.ai_utility_model,
            )
        )
        steps = self._parse_steps(response.text)
        return Plan(objective=objective, steps=steps)

    @staticmethod
    def _parse_steps(text: str) -> list[PlanStep]:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Planner returned no JSON plan")
        raw = json.loads(text[start : end + 1])
        raw_steps = raw.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("Plan contains no steps")

        steps: list[PlanStep] = []
        for index, item in enumerate(raw_steps[:12]):
            title = str(item.get("title", "")).strip()
            if not title:
                raise ValueError(f"Plan step {index} has no title")
            depends_on = [
                dep
                for dep in item.get("depends_on", [])
                if isinstance(dep, int) and 0 <= dep < index
            ]
            steps.append(
                PlanStep(
                    title=title[:300],
                    description=str(item.get("description", "")).strip(),
                    depends_on=depends_on,
                )
            )
        return steps
