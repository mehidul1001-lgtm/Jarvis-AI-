"""Schemas for chat, memory, tasks and agents."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.memory import MemoryKind
from app.models.task import TaskStatus

# --- Chat -------------------------------------------------------------------


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    summary: str | None
    total_input_tokens: int
    total_output_tokens: int
    created_at: datetime
    updated_at: datetime


class MessageSend(BaseModel):
    text: str = Field(min_length=1, max_length=32000)

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class MessageMeta(BaseModel):
    confidence: float | None = None
    latency_ms: float | None = None
    tool_calls: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    revised: bool = False


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime | None
    meta: MessageMeta


# --- Memory ---------------------------------------------------------------


class MemoryCreate(BaseModel):
    kind: MemoryKind
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    detail: dict[str, Any] | None = None


class MemoryUpdate(BaseModel):
    kind: MemoryKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    detail: dict[str, Any] | None = None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MemoryKind
    title: str
    content: str
    importance: float
    source: str | None
    detail: dict[str, Any] | None
    access_count: int
    created_at: datetime
    updated_at: datetime


class MemorySearchOut(BaseModel):
    entry: MemoryOut
    score: float


class MemoryImport(BaseModel):
    items: list[dict[str, Any]] = Field(max_length=5000)


# --- Tasks ---------------------------------------------------------------


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    agent: str = Field(pattern="^(amazon|finance|developer|research|operations|marketing)$")
    objective: str = Field(min_length=1, max_length=8000)
    priority: int = Field(default=5, ge=0, le=10)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    handler: str
    status: TaskStatus
    priority: int
    progress: float
    progress_note: str | None
    attempts: int
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# --- Agents ---------------------------------------------------------------


class AgentStatusOut(BaseModel):
    name: str
    display_name: str
    description: str
    healthy: bool
    available: bool
    last_heartbeat: datetime | None
    runs_completed: int
    runs_failed: int
    tools: list[str]
