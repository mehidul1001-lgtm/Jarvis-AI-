"""Operations Agent: daily reports, reminders, maintenance workflows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.agents.base import BaseAgent
from app.ai.tools.registry import ToolContext, ToolRegistry


class OperationsAgent(BaseAgent):
    name = "operations"
    display_name = "Operations Agent"
    description = "Daily reports, reminders, workflow automation and system upkeep."
    persona = (
        "You are the operations specialist. You produce daily/weekly business "
        "summaries from stored memories and task history, set reminders (as "
        "scheduled tasks), and keep the workspace tidy (memory maintenance). "
        "When asked for a report, gather data with memory_search and list_tasks, "
        "then produce a structured, scannable brief."
    )

    def register_domain_tools(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="schedule_reminder",
            description=(
                "Schedule a reminder for the user. It becomes a queued operations "
                "task that fires at the requested time (hours from now)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reminder": {"type": "string"},
                    "hours_from_now": {"type": "number", "description": "0.1 to 720"},
                },
                "required": ["reminder", "hours_from_now"],
                "additionalProperties": False,
            },
        )
        async def schedule_reminder(ctx: ToolContext, reminder: str, hours_from_now: float) -> str:
            from app.services.task_service import TaskService

            hours = min(max(hours_from_now, 0.1), 720)
            when = datetime.now(UTC) + timedelta(hours=hours)
            task = await TaskService(ctx.db).create(
                ctx.user,
                title=f"Reminder: {reminder[:200]}",
                handler="operations.remind",
                payload={"reminder": reminder},
                scheduled_for=when,
            )
            return json.dumps({"task_id": str(task.id), "fires_at": when.isoformat()})

        @registry.tool(
            name="memory_maintenance",
            description=(
                "Run memory maintenance: decay the importance of stale, unused "
                "memories so retrieval stays sharp. Returns how many entries aged."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
        async def memory_maintenance(ctx: ToolContext) -> str:
            from app.services.memory_service import MemoryService

            count = await MemoryService(ctx.db).age_memories(ctx.user)
            return f"Aged {count} stale memory entries."


AGENT = OperationsAgent
