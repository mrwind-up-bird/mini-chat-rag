"""Message model — a single turn in a Chat conversation."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(TimestampMixin, SQLModel, table=True):
    __tablename__ = "messages"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    chat_id: uuid.UUID = Field(foreign_key="chats.id", nullable=False, index=True)

    role: MessageRole = Field(nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))

    # Token counts for this specific message (set for assistant messages)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)

    # Retrieved context chunk IDs used for this response (JSON array of UUIDs)
    context_chunks: str = Field(default="[]", sa_column=Column(Text, nullable=False, server_default="[]"))

    # User feedback on assistant messages: "positive", "negative", or None
    feedback: str | None = Field(default=None, max_length=20)


# ── Pydantic schemas ─────────────────────────────────────────

class MessageRead(SQLModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    role: MessageRole
    content: str
    prompt_tokens: int
    completion_tokens: int
    feedback: str | None = None
    created_at: datetime
