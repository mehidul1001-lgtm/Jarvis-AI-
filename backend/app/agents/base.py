"""Agent framework base class.

Every agent bundles: a planner (objective decomposition), an executor (the
brain's reason/act loop with the agent's tool set), a validator (reflection
over the produced result), a memory connector (outcomes written to long-term
memory), structured logging, an error handler with retry, a status monitor
with heartbeat and health check, and a lightweight event system.

Agents are independently loadable: each module exposes ``AGENT`` and the
registry imports them by name.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.ai.brain import Brain
from app.ai.llm import get_llm_client
from app.ai.planner import PlanningEngine
from app.ai.tools.builtin import builtin_registry
from app.ai.tools.registry import ToolRegistry
from app.workflows.engine import TaskRunContext

logger = logging.getLogger("jarvis.agents")

AgentEventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

# Objectives longer than this get decomposed into steps before execution.
DECOMPOSE_THRESHOLD_CHARS = 240
STEP_RETRIES = 1


@dataclass
class AgentStats:
    runs_completed: int = 0
    runs_failed: int = 0
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    listeners: list[AgentEventHandler] = field(default_factory=list)


class BaseAgent:
    """Subclasses set the class attributes and optionally add domain tools."""

    name: str = "base"
    display_name: str = "Base Agent"
    description: str = ""
    persona: str = ""

    def __init__(self) -> None:
        self.stats = AgentStats()
        domain = ToolRegistry(f"{self.name}-domain")
        self.register_domain_tools(domain)
        self.tools = builtin_registry.merged_with(domain, f"{self.name}-tools")
        self.log = logging.getLogger(f"jarvis.agents.{self.name}")

    # --- Extension points -----------------------------------------------------

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        """Override to add agent-specific tools."""

    # --- Event system ------------------------------------------------------------

    def on_event(self, handler: AgentEventHandler) -> None:
        self.stats.listeners.append(handler)

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        for handler in list(self.stats.listeners):
            try:
                await handler(event, data)
            except Exception:
                self.log.exception("Agent event listener failed for '%s'", event)

    # --- Status monitor / heartbeat / health ------------------------------------------

    def heartbeat(self) -> None:
        self.stats.last_heartbeat = datetime.now(UTC)

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "healthy": self.stats.last_error is None or self.stats.runs_completed > 0,
            "available": get_llm_client().available,
            "last_heartbeat": self.stats.last_heartbeat,
            "runs_completed": self.stats.runs_completed,
            "runs_failed": self.stats.runs_failed,
            "tools": sorted(spec.name for spec in self.tools._tools.values()),  # noqa: SLF001
        }

    # --- Execution ---------------------------------------------------------------

    async def run(self, ctx: TaskRunContext) -> dict[str, Any]:
        """Execute a workflow task: plan → execute → validate → remember."""
        objective = str((ctx.task.payload or {}).get("objective") or ctx.task.description or "")
        if not objective.strip():
            raise ValueError("Task has no objective")

        self.heartbeat()
        await self.emit("run.started", {"task_id": str(ctx.task.id), "objective": objective})
        self.log.info("Run started (task=%s)", ctx.task.id)

        try:
            steps = await self._plan(objective)
            outputs: list[dict[str, Any]] = []
            for index, step in enumerate(steps):
                await ctx.report(
                    index / max(1, len(steps)), f"Step {index + 1}/{len(steps)}: {step}"
                )
                output = await self._execute_step(ctx, step, prior=outputs)
                outputs.append(output)
                self.heartbeat()

            summary, confidence = await self._validate(ctx, objective, outputs)
            await self._remember(ctx, objective, summary, confidence)

            self.stats.runs_completed += 1
            self.stats.last_error = None
            await self.emit(
                "run.completed", {"task_id": str(ctx.task.id), "confidence": confidence}
            )
            return {
                "agent": self.name,
                "summary": summary,
                "confidence": confidence,
                "steps": [
                    {"objective": step_output["step"], "ok": not step_output["failed"]}
                    for step_output in outputs
                ],
            }
        except asyncio.CancelledError:
            await self.emit("run.cancelled", {"task_id": str(ctx.task.id)})
            raise
        except Exception as exc:
            # Error handler: record, emit, then let the workflow engine retry.
            self.stats.runs_failed += 1
            self.stats.last_error = str(exc)[:500]
            await self.emit("run.failed", {"task_id": str(ctx.task.id), "error": str(exc)})
            self.log.exception("Run failed (task=%s)", ctx.task.id)
            raise

    # --- Planner -------------------------------------------------------------------

    async def _plan(self, objective: str) -> list[str]:
        if len(objective) < DECOMPOSE_THRESHOLD_CHARS:
            return [objective]
        try:
            plan = await PlanningEngine().decompose(objective, context=self.persona)
            return [f"{step.title}. {step.description}".strip() for step in plan.steps] or [
                objective
            ]
        except Exception:
            self.log.warning("Planning failed; executing objective as a single step")
            return [objective]

    # --- Executor with retry ------------------------------------------------------------

    async def _execute_step(
        self, ctx: TaskRunContext, step: str, prior: list[dict[str, Any]]
    ) -> dict[str, Any]:
        context_note = ""
        if prior:
            done = "\n".join(f"- {p['step']}: {p['text'][:300]}" for p in prior[-3:])
            context_note = f"\n\nAlready completed steps and their results:\n{done}"

        last_error: Exception | None = None
        for attempt in range(STEP_RETRIES + 1):
            try:
                brain = Brain(
                    ctx.db,
                    ctx.user,
                    tools=self.tools,
                    persona=self.persona,
                    agent=self.name,
                )
                result = await brain.run_objective(step + context_note)
                failed = not result.text.strip()
                return {"step": step, "text": result.text, "failed": failed}
            except Exception as exc:  # retry once on transient failures
                last_error = exc
                self.log.warning(
                    "Step failed (attempt %s/%s): %s", attempt + 1, STEP_RETRIES + 1, exc
                )
                await asyncio.sleep(1 + attempt)
        raise RuntimeError(f"Step '{step[:80]}' failed: {last_error}") from last_error

    # --- Validator -----------------------------------------------------------------------

    async def _validate(
        self, ctx: TaskRunContext, objective: str, outputs: list[dict[str, Any]]
    ) -> tuple[str, float]:
        combined = "\n\n".join(output["text"] for output in outputs if output["text"])
        failures = sum(1 for output in outputs if output["failed"])
        confidence = max(0.1, 0.9 - 0.2 * failures)
        summary = combined[:4000] if combined else "No output produced."
        return summary, round(confidence, 2)

    # --- Memory connector -------------------------------------------------------------------

    async def _remember(
        self, ctx: TaskRunContext, objective: str, summary: str, confidence: float
    ) -> None:
        from app.models.memory import MemoryKind
        from app.services.memory_service import MemoryService

        try:
            await MemoryService(ctx.db).create(
                ctx.user,
                kind=MemoryKind.TASK_NOTE,
                title=f"[{self.display_name}] {objective[:200]}",
                content=summary[:8000],
                importance=0.4,
                source=f"agent:{self.name}",
                detail={"task_id": str(ctx.task.id), "confidence": confidence},
            )
        except Exception:
            self.log.warning("Could not write task outcome to memory")
