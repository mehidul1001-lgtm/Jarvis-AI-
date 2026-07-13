"""Conversations: CRUD plus the chat turn orchestration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.brain import Brain
from app.core.exceptions import NotFoundError
from app.core.realtime import manager
from app.models.conversation import Conversation, Message, MessageRole
from app.models.user import User

logger = logging.getLogger("jarvis.conversations")


class ConversationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- CRUD -------------------------------------------------------------

    async def create(self, user: User, title: str | None = None) -> Conversation:
        conversation = Conversation(
            user_id=user.id, title=(title or "New conversation").strip()[:300]
        )
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def get(self, user: User, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise NotFoundError("Conversation not found")
        return conversation

    async def list(
        self, user: User, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        query = select(Conversation).where(
            Conversation.user_id == user.id, Conversation.is_archived.is_(False)
        )
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(Conversation.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def rename(self, user: User, conversation_id: uuid.UUID, title: str) -> Conversation:
        conversation = await self.get(user, conversation_id)
        conversation.title = title.strip()[:300]
        await self.db.flush()
        return conversation

    async def delete(self, user: User, conversation_id: uuid.UUID) -> None:
        conversation = await self.get(user, conversation_id)
        await self.db.delete(conversation)
        await self.db.flush()

    async def messages(
        self, user: User, conversation_id: uuid.UUID, *, limit: int = 200
    ) -> list[Message]:
        conversation = await self.get(user, conversation_id)
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- Chat turn ----------------------------------------------------------

    async def append_message(
        self,
        conversation: Conversation,
        role: MessageRole,
        content: str,
        *,
        blocks: list | None = None,
        meta: dict | None = None,
    ) -> Message:
        next_seq = (
            await self.db.execute(
                select(func.coalesce(func.max(Message.sequence), 0)).where(
                    Message.conversation_id == conversation.id
                )
            )
        ).scalar_one() + 1
        message = Message(
            conversation_id=conversation.id,
            sequence=next_seq,
            role=role,
            content=content,
            blocks=blocks,
            meta=meta,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def send(
        self, user: User, conversation_id: uuid.UUID, text: str
    ) -> tuple[Message, Message]:
        """Run one full chat turn; returns (user_message, assistant_message)."""
        conversation = await self.get(user, conversation_id)
        text = text.strip()

        user_message = await self.append_message(conversation, MessageRole.USER, text)
        if conversation.title == "New conversation":
            conversation.title = text[:80] or "New conversation"

        async def forward(event: dict[str, Any]) -> None:
            await manager.send_to_user(user.id, {**event, "conversation_id": str(conversation.id)})

        brain = Brain(self.db, user)
        result = await brain.respond(conversation, text, on_event=forward)

        assistant_message = await self.append_message(
            conversation,
            MessageRole.ASSISTANT,
            result.text,
            blocks=result.blocks,
            meta=result.meta,
        )
        conversation.total_input_tokens += int(result.meta.get("input_tokens") or 0)
        conversation.total_output_tokens += int(result.meta.get("output_tokens") or 0)
        await self.db.flush()

        await brain.compress_if_needed(conversation)

        await manager.send_to_user(
            user.id,
            {
                "type": "chat.message",
                "conversation_id": str(conversation.id),
                "message": serialize_message(assistant_message),
            },
        )
        return user_message, assistant_message


def serialize_message(message: Message) -> dict[str, Any]:
    meta = message.meta or {}
    return {
        "id": str(message.id),
        "role": message.role.value,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "meta": {
            "confidence": meta.get("confidence"),
            "latency_ms": meta.get("latency_ms"),
            "tool_calls": [call.get("name") for call in meta.get("tool_trace", [])],
            "input_tokens": meta.get("input_tokens"),
            "output_tokens": meta.get("output_tokens"),
            "revised": meta.get("revised", False),
        },
    }
