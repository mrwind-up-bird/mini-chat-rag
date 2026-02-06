"""Chat model — a conversation session between a user and a bot."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class Chat(TimestampMixin, SQLModel, table=True):
    __tablename__ = "chats"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    bot_profile_id: uuid.UUID = Field(foreign_key="bot_profiles.id", nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False, index=True)

    title: str = Field(default="", max_length=500)
    message_count: int = Field(default=0)
    total_prompt_tokens: int = Field(default=0)
    total_completion_tokens: int = Field(default=0)


# ── Pydantic schemas ─────────────────────────────────────────

class ChatRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    bot_profile_id: uuid.UUID
    title: str
    message_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    created_at: datetime
    updated_at: datetime
