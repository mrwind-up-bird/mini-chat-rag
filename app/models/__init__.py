"""Import all models so SQLModel.metadata picks them up."""

from app.models.api_token import ApiToken, ApiTokenCreate, ApiTokenCreated, ApiTokenRead
from app.models.bot_profile import BotProfile, BotProfileCreate, BotProfileRead, BotProfileUpdate
from app.models.chat import Chat, ChatRead
from app.models.chunk import Chunk, ChunkRead
from app.models.document import Document, DocumentRead
from app.models.message import Message, MessageRead, MessageRole
from app.models.source import (
    Source,
    SourceCreate,
    SourceRead,
    SourceStatus,
    SourceType,
    SourceUpdate,
)
from app.models.tenant import Tenant, TenantCreate, TenantRead
from app.models.usage_event import UsageEvent, UsageEventRead
from app.models.user import User, UserCreate, UserRead, UserRole
from app.models.webhook import Webhook, WebhookCreate, WebhookCreated, WebhookEvent, WebhookRead

__all__ = [
    "ApiToken",
    "ApiTokenCreate",
    "ApiTokenCreated",
    "ApiTokenRead",
    "BotProfile",
    "BotProfileCreate",
    "BotProfileRead",
    "BotProfileUpdate",
    "Chat",
    "ChatRead",
    "Chunk",
    "ChunkRead",
    "Document",
    "DocumentRead",
    "Message",
    "MessageRead",
    "MessageRole",
    "Source",
    "SourceCreate",
    "SourceRead",
    "SourceStatus",
    "SourceType",
    "SourceUpdate",
    "Tenant",
    "TenantCreate",
    "TenantRead",
    "UsageEvent",
    "UsageEventRead",
    "User",
    "UserCreate",
    "UserRead",
    "UserRole",
    "Webhook",
    "WebhookCreate",
    "WebhookCreated",
    "WebhookEvent",
    "WebhookRead",
]
