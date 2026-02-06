"""User model — belongs to a tenant."""

import uuid
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, new_uuid


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class User(TimestampMixin, SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", nullable=False, index=True)
    email: str = Field(max_length=320, nullable=False, index=True)
    password_hash: str = Field(nullable=False)
    display_name: str = Field(default="", max_length=255)
    role: UserRole = Field(default=UserRole.MEMBER)
    is_active: bool = Field(default=True)


# ── Pydantic schemas ─────────────────────────────────────────

class UserCreate(SQLModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=255)
    role: UserRole = UserRole.MEMBER


class UserRead(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
