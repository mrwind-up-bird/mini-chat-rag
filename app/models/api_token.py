"""API token model — bearer tokens for programmatic access."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class ApiToken(TimestampMixin, SQLModel, table=True):
    __tablename__ = "api_tokens"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False, index=True)

    # Human-readable label, e.g. "production-bot-1"
    name: str = Field(max_length=255, nullable=False)

    # SHA-256 hash of the raw token — raw value is shown only once at creation
    token_hash: str = Field(nullable=False, unique=True, index=True)

    # Prefix stored for identification (first 8 chars, e.g. "mrag_abc1")
    token_prefix: str = Field(max_length=12, nullable=False)

    is_active: bool = Field(default=True)
    expires_at: datetime | None = Field(default=None)
    last_used_at: datetime | None = Field(default=None)


# ── Pydantic schemas ─────────────────────────────────────────

class ApiTokenCreate(SQLModel):
    name: str = Field(max_length=255)


class ApiTokenRead(SQLModel):
    """Returned on list / detail — never includes the raw token."""
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    token_prefix: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiTokenCreated(ApiTokenRead):
    """Returned exactly once at creation time — includes the raw token."""
    raw_token: str
