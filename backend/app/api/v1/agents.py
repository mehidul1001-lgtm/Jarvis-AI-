"""Agent status API."""

from __future__ import annotations

from fastapi import APIRouter

from app.agents.registry import get_agent_registry
from app.api.deps import CurrentUser
from app.schemas.ai import AgentStatusOut

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentStatusOut])
async def list_agents(user: CurrentUser) -> list[AgentStatusOut]:
    return [AgentStatusOut.model_validate(h) for h in get_agent_registry().health()]
