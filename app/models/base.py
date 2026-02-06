"""Shared base fields for all models."""

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin(SQLModel):
    """Created / updated timestamps injected into every table."""

    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
