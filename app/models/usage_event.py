"""UsageEvent model — tracks LLM token consumption per request."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class UsageEvent(TimestampMixin, SQLModel, table=True):
    __tablename__ = "usage_events"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    chat_id: uuid.UUID = Field(foreign_key="chats.id", nullable=False, index=True)
    message_id: uuid.UUID = Field(foreign_key="messages.id", nullable=False, index=True)
    bot_profile_id: uuid.UUID = Field(foreign_key="bot_profiles.id", nullable=False, index=True)

    model: str = Field(max_length=100, nullable=False)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)


# ── Pydantic schemas ─────────────────────────────────────────

class UsageEventRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    chat_id: uuid.UUID
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    created_at: datetime
