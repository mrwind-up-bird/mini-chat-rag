"""BotProfile model — configures an AI assistant per tenant."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class BotProfile(TimestampMixin, SQLModel, table=True):
    __tablename__ = "bot_profiles"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)

    name: str = Field(max_length=255, nullable=False)
    description: str = Field(default="", max_length=1000)

    # LLM configuration
    model: str = Field(default="gpt-4o-mini", max_length=100)
    system_prompt: str = Field(default="You are a helpful assistant.")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=128000)

    # Provider credentials — Fernet-encrypted JSON blob.
    # Stores e.g. {"api_key": "sk-..."} as ciphertext.
    # NULL means "use platform default credentials".
    encrypted_credentials: str | None = Field(default=None)

    is_active: bool = Field(default=True)


# ── Pydantic schemas ─────────────────────────────────────────

class BotProfileCreate(SQLModel):
    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=1000)
    model: str = Field(default="gpt-4o-mini", max_length=100)
    system_prompt: str = Field(default="You are a helpful assistant.")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=128000)
    credentials: dict | None = Field(default=None, description="Provider credentials (encrypted at rest)")


class BotProfileUpdate(SQLModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    model: str | None = Field(default=None, max_length=100)
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    credentials: dict | None = Field(default=None, description="Set to {} to clear credentials")
    is_active: bool | None = None


class BotProfileRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    model: str
    system_prompt: str
    temperature: float
    max_tokens: int
    has_credentials: bool = Field(description="True if custom provider credentials are set")
    is_active: bool
    created_at: datetime
    updated_at: datetime
