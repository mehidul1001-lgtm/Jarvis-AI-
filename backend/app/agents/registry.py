"""Agent registry: loads agent modules and dispatches workflow tasks to them."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from app.agents.base import BaseAgent
from app.workflows.engine import TaskRunContext, WorkflowEngine

logger = logging.getLogger("jarvis.agents.registry")

# Each module exposes AGENT (a BaseAgent subclass); independently loadable.
AGENT_MODULES = [
    "app.agents.amazon",
    "app.agents.finance",
    "app.agents.developer",
    "app.agents.research",
    "app.agents.operations",
    "app.agents.marketing",
]


class AgentRegistry:
    def __init__(self) -> None:
        self.agents: dict[str, BaseAgent] = {}

    def load_all(self) -> None:
        for module_name in AGENT_MODULES:
            try:
                module = importlib.import_module(module_name)
                agent_cls = module.AGENT
                agent = agent_cls()
                self.agents[agent.name] = agent
                logger.info("Loaded agent '%s'", agent.name)
            except Exception:
                # One broken agent must not take the platform down.
                logger.exception("Failed to load agent module %s", module_name)

    def get(self, name: str) -> BaseAgent | None:
        return self.agents.get(name)

    def health(self) -> list[dict[str, Any]]:
        return [agent.health() for agent in self.agents.values()]


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _registry.load_all()
    return _registry


def register_workflow_handlers(engine: WorkflowEngine) -> None:
    """Wire the standard task handlers into the workflow engine."""

    async def run_agent(ctx: TaskRunContext) -> dict[str, Any] | None:
        agent_name = str((ctx.task.payload or {}).get("agent", ""))
        agent = get_agent_registry().get(agent_name)
        if agent is None:
            raise RuntimeError(f"Unknown agent '{agent_name}'")
        return await agent.run(ctx)

    async def remind(ctx: TaskRunContext) -> dict[str, Any] | None:
        from app.core.realtime import manager

        reminder = str((ctx.task.payload or {}).get("reminder", ctx.task.title))
        await manager.send_to_user(
            ctx.user.id,
            {"type": "notification", "title": "Reminder", "body": reminder},
        )
        return {"delivered": True, "reminder": reminder}

    async def memory_age(ctx: TaskRunContext) -> dict[str, Any] | None:
        from app.services.memory_service import MemoryService

        count = await MemoryService(ctx.db).age_memories(ctx.user)
        return {"aged": count}

    engine.register_handler("agent.run", run_agent)
    engine.register_handler("operations.remind", remind)
    engine.register_handler("memory.age", memory_age)
