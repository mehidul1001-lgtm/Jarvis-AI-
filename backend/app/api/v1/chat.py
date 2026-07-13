"""Chat API: conversations and messages."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.ai import (
    ConversationCreate,
    ConversationOut,
    ConversationRename,
    MessageOut,
    MessageSend,
)
from app.schemas.common import Message as MessageEnvelope
from app.schemas.common import Page
from app.services.conversation_service import ConversationService, serialize_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, user: CurrentUser, db: DbSession
) -> ConversationOut:
    conversation = await ConversationService(db).create(user, payload.title)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[ConversationOut]:
    conversations, total = await ConversationService(db).list(user, page=page, page_size=page_size)
    return Page(
        items=[ConversationOut.model_validate(c) for c in conversations],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConversationOut:
    conversation = await ConversationService(db).get(user, conversation_id)
    return ConversationOut.model_validate(conversation)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationRename,
    user: CurrentUser,
    db: DbSession,
) -> ConversationOut:
    conversation = await ConversationService(db).rename(user, conversation_id, payload.title)
    return ConversationOut.model_validate(conversation)


@router.delete("/conversations/{conversation_id}", response_model=MessageEnvelope)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageEnvelope:
    await ConversationService(db).delete(user, conversation_id)
    return MessageEnvelope(message="Conversation deleted")


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[MessageOut]:
    messages = await ConversationService(db).messages(user, conversation_id)
    return [MessageOut.model_validate(serialize_message(m)) for m in messages]


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut)
async def send_message(
    conversation_id: uuid.UUID, payload: MessageSend, user: CurrentUser, db: DbSession
) -> MessageOut:
    """Run one chat turn. Tokens stream over the WebSocket; the final
    assistant message is returned here (and also pushed as ``chat.message``)."""
    _, assistant = await ConversationService(db).send(user, conversation_id, payload.text)
    return MessageOut.model_validate(serialize_message(assistant))
