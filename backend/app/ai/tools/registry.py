"""Tool registry: definition, permission checks, safe execution, audit.

Tools are the only way the model can act on the system. Every execution is
permission-checked against the calling user's role, validated, timed, and
written to the audit trail — the model itself can never bypass this layer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.services.audit_service import AuditService

logger = logging.getLogger("jarvis.ai.tools")

ALL_ROLES = (UserRole.ADMIN, UserRole.MANAGER, UserRole.USER)

ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class ToolContext:
    """Everything a tool handler may need, scoped to one request/task."""

    db: AsyncSession
    user: User
    conversation_id: uuid.UUID | None = None
    agent: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    allowed_roles: tuple[UserRole, ...] = ALL_ROLES
    destructive: bool = False

    def definition(self) -> dict[str, Any]:
        schema = dict(self.input_schema)
        schema.setdefault("type", "object")
        schema.setdefault("additionalProperties", False)
        schema.setdefault("required", [])
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": schema,
        }


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class ToolRegistry:
    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered in '{self.name}'")
        self._tools[spec.name] = spec

    def tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        allowed_roles: tuple[UserRole, ...] = ALL_ROLES,
        destructive: bool = False,
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Decorator form of :meth:`register`."""

        def decorator(handler: ToolHandler) -> ToolHandler:
            self.register(
                ToolSpec(
                    name=name,
                    description=description,
                    input_schema=input_schema,
                    handler=handler,
                    allowed_roles=allowed_roles,
                    destructive=destructive,
                )
            )
            return handler

        return decorator

    def merged_with(self, other: ToolRegistry, name: str) -> ToolRegistry:
        merged = ToolRegistry(name)
        merged._tools = {**self._tools, **other._tools}
        return merged

    def specs_for(self, user: User) -> list[ToolSpec]:
        return [spec for spec in self._tools.values() if user.role in spec.allowed_roles]

    def definitions_for(self, user: User) -> list[dict[str, Any]]:
        # Deterministic order keeps the prompt prefix cache-friendly.
        return [spec.definition() for spec in sorted(self.specs_for(user), key=lambda s: s.name)]

    async def execute(self, name: str, tool_input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec = self._tools.get(name)
        audit = AuditService(ctx.db)
        started = time.perf_counter()

        if spec is None:
            return ToolResult(f"Error: unknown tool '{name}'", is_error=True)
        if ctx.user.role not in spec.allowed_roles:
            await audit.record(
                "ai.tool_denied",
                user_id=ctx.user.id,
                resource=f"tool:{name}",
                detail={"role": ctx.user.role.value, "agent": ctx.agent},
            )
            return ToolResult(f"Error: your role does not permit the '{name}' tool", is_error=True)

        try:
            kwargs = self._validated_kwargs(spec, tool_input)
            result = spec.handler(ctx, **kwargs)
            content = await result if inspect.isawaitable(result) else result
            is_error = False
        except ToolInputError as exc:
            content, is_error = f"Error: {exc}", True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Tool '%s' failed", name)
            content, is_error = f"Error: tool '{name}' failed unexpectedly", True

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        await audit.record(
            "ai.tool_call",
            user_id=ctx.user.id,
            resource=f"tool:{name}",
            detail={
                "agent": ctx.agent,
                "conversation_id": str(ctx.conversation_id) if ctx.conversation_id else None,
                "input": _compact(tool_input),
                "is_error": is_error,
                "duration_ms": duration_ms,
            },
        )
        return ToolResult(content=str(content), is_error=is_error)

    @staticmethod
    def _validated_kwargs(spec: ToolSpec, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Defense in depth on top of the API's strict schemas."""
        if not isinstance(tool_input, dict):
            raise ToolInputError("tool input must be an object")
        schema = spec.input_schema
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in tool_input:
                raise ToolInputError(f"missing required parameter '{required}'")
        return {key: value for key, value in tool_input.items() if key in properties}


class ToolInputError(Exception):
    pass


def _compact(value: dict[str, Any], limit: int = 800) -> dict[str, Any] | str:
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return value
    return text[:limit] + "…"
