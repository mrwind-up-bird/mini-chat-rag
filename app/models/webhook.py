from ipaddress import AddressValueError, IPv4Address, IPv6Address
from urllib.parse import urlparse
"""Webhook model for event notifications."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Text
from sqlmodel import Column, Field, SQLModel

from app.models.base import TimestampMixin, new_uuid

def validate_webhook_url(url: str) -> str:
    """Validate webhook URL to prevent SSRF attacks."""
    try:
        parsed = urlparse(url)
        
        # Only allow http/https schemes
        if parsed.scheme not in ('http', 'https'):
            raise ValueError("Only http and https schemes are allowed")
        
        # Block internal/private IP addresses
        if parsed.hostname:
            try:
                ip = IPv4Address(parsed.hostname)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    raise ValueError("Private, loopback, and link-local addresses are not allowed")
            except AddressValueError:
                try:
                    ip = IPv6Address(parsed.hostname)
                    if ip.is_private or ip.is_loopback or ip.is_link_local:
                        raise ValueError("Private, loopback, and link-local addresses are not allowed")
                except AddressValueError:
                    # Not an IP address, allow domain names
                    pass
        
        return url
    except Exception as e:
        raise ValueError(f"Invalid webhook URL: {e}")



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
    url: str = Field(max_length=2048, json_schema_extra={"validator": validate_webhook_url})
    is_active: bool = Field(default=True)
    description: str = Field(default="", max_length=500)


# ── Pydantic schemas ─────────────────────────────────────────


class WebhookCreate(SQLModel):
    url: str = Field(max_length=2048)
    events: list[str]
    url: str = Field(max_length=2048, json_schema_extra={"validator": validate_webhook_url})
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
