"""Source model — a data source attached to a bot profile."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class SourceType(StrEnum):
    TEXT = "text"
    UPLOAD = "upload"
    URL = "url"


class SourceStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class Source(TimestampMixin, SQLModel, table=True):
    __tablename__ = "sources"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    bot_profile_id: uuid.UUID = Field(foreign_key="bot_profiles.id", nullable=False, index=True)
    parent_id: uuid.UUID | None = Field(
        default=None, foreign_key="sources.id", nullable=True, index=True,
    )

    name: str = Field(max_length=255, nullable=False)
    description: str = Field(default="", max_length=1000)
    source_type: SourceType = Field(nullable=False)
    status: SourceStatus = Field(default=SourceStatus.PENDING)

    # Type-specific config stored as JSON text.
    # e.g. {"url": "https://..."} or {"filename": "doc.pdf", "content_type": "application/pdf"}
    config: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))

    # Inline text content for source_type="text"
    content: str | None = Field(default=None, sa_column=Column(Text))

    # Tracking
    document_count: int = Field(default=0)
    chunk_count: int = Field(default=0)
    error_message: str | None = Field(default=None, max_length=2000)

    is_active: bool = Field(default=True)


# ── Pydantic schemas ─────────────────────────────────────────

class SourceCreate(SQLModel):
    bot_profile_id: uuid.UUID
    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=1000)
    source_type: SourceType
    config: dict = Field(default_factory=dict)
    content: str | None = None
    parent_id: uuid.UUID | None = None


class SourceUpdate(SQLModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    config: dict | None = None
    content: str | None = None
    is_active: bool | None = None


class SourceRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    bot_profile_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    name: str
    description: str
    source_type: SourceType
    status: SourceStatus
    config: dict
    document_count: int
    chunk_count: int
    error_message: str | None
    is_active: bool
    children_count: int = 0
    created_at: datetime
    updated_at: datetime
