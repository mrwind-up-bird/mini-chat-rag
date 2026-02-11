"""Webhook model for event notifications."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class WebhookEvent(StrEnum):
    SOURCE_INGESTED = "source.ingested"
    SOURCE_FAILED = "source.failed"
    CHAT_MESSAGE = "chat.message"


class Webhook(TimestampMixin, SQLModel, table=True):
    __tablename__ = "webhooks"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    url: str = Field(max_length=2048)
    secret: str = Field(max_length=256)  # for HMAC signing
    events: str = Field(sa_column=Column(Text, nullable=False))  # JSON array of event types
    is_active: bool = Field(default=True)
    description: str = Field(default="", max_length=500)


# ── Pydantic schemas ─────────────────────────────────────────


class WebhookCreate(SQLModel):
    url: str = Field(max_length=2048)
    events: list[str]
    description: str = Field(default="", max_length=500)
    secret: str | None = None


class WebhookRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    url: str
    events: list[str]
    is_active: bool
    description: str
    has_secret: bool
    created_at: datetime
    updated_at: datetime


class WebhookCreated(WebhookRead):
    """Returned exactly once at creation time — includes the raw secret."""
    secret: str
