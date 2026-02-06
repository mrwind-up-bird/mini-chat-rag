"""Chunk model — an indexed text segment stored in both SQL and Qdrant."""

import uuid
from datetime import datetime

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class Chunk(TimestampMixin, SQLModel, table=True):
    __tablename__ = "chunks"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", nullable=False, index=True)
    source_id: uuid.UUID = Field(foreign_key="sources.id", nullable=False, index=True)

    # Position within the document
    chunk_index: int = Field(nullable=False)

    content: str = Field(sa_column=Column(Text, nullable=False))
    char_count: int = Field(default=0)

    # Reference to the Qdrant point (same as chunk.id for simplicity)
    qdrant_point_id: str = Field(default="", max_length=100)

    # Metadata stored alongside the vector in Qdrant payload
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))


# ── Pydantic schemas ─────────────────────────────────────────

class ChunkRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    document_id: uuid.UUID
    source_id: uuid.UUID
    chunk_index: int
    content: str
    char_count: int
    created_at: datetime
