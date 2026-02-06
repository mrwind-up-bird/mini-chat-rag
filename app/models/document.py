"""Document model — a parsed unit of content from a Source."""

import uuid
from datetime import datetime

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class Document(TimestampMixin, SQLModel, table=True):
    __tablename__ = "documents"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    source_id: uuid.UUID = Field(foreign_key="sources.id", nullable=False, index=True)

    title: str = Field(default="", max_length=500)
    raw_content: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))

    # Metadata from the source (e.g. filename, page number, URL)
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))

    char_count: int = Field(default=0)
    chunk_count: int = Field(default=0)


# ── Pydantic schemas ─────────────────────────────────────────

class DocumentRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    title: str
    char_count: int
    chunk_count: int
    created_at: datetime
