"""Tenant model — top-level isolation boundary."""

import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class Tenant(TimestampMixin, SQLModel, table=True):
    __tablename__ = "tenants"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=255, nullable=False)
    slug: str = Field(max_length=100, unique=True, nullable=False, index=True)
    is_active: bool = Field(default=True)

    # Billing / plan metadata (extensible later)
    plan: str = Field(default="free", max_length=50)


# ── Pydantic schemas (read / create) ─────────────────────────

class TenantCreate(SQLModel):
    name: str = Field(max_length=255)
    slug: str = Field(max_length=100)


class TenantRead(SQLModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    plan: str
