"""Chat endpoint — the main RAG interaction point."""

import json
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import decrypt_value
from app.models.bot_profile import BotProfile
from app.models.chat import Chat, ChatRead
from app.models.message import Message, MessageRead, MessageRole
from app.models.usage_event import UsageEvent
from app.services.orchestrator import run_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


# ── List chats ────────────────────────────────────────────────

@router.get("", response_model=list[ChatRead])
async def list_chats(
    auth: Auth,
    session: Session,
    bot_profile_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ChatRead]:
    """List chat sessions for the current tenant."""
    stmt = select(Chat).where(Chat.tenant_id == auth.tenant_id)
    if bot_profile_id:
        stmt = stmt.where(Chat.bot_profile_id == bot_profile_id)
    stmt = (
        stmt
        .order_by(Chat.created_at.desc())  # type: ignore[union-attr]
        .limit(min(limit, 100))
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [ChatRead.model_validate(c) for c in result.scalars().all()]


# ── Request / Response schemas ────────────────────────────────

class ChatRequest(BaseModel):
    bot_profile_id: uuid.UUID
    message: str = Field(min_length=1, max_length=32000)
    chat_id: uuid.UUID | None = Field(
        default=None,
        description="Existing chat session ID. Omit to start a new conversation.",
    )


class ChatMessageResponse(BaseModel):
    chat_id: uuid.UUID
    message: MessageRead
    sources: list[dict] = Field(
        default_factory=list,
        description="Retrieved context chunks used for the answer",
    )
    usage: dict = Field(default_factory=dict)


# ── Route ─────────────────────────────────────────────────────

@router.post("", response_model=ChatMessageResponse, status_code=status.HTTP_200_OK)
async def chat(
    body: ChatRequest,
    auth: Auth,
    session: Session,
) -> ChatMessageResponse:
    """Send a message and get a RAG-augmented response.

    Creates a new chat session if chat_id is not provided.
    Retrieves relevant context from the knowledge base, then generates
    a response using the configured LLM.
    """
    tenant_id = auth.tenant_id

    # 1. Load bot profile (tenant-scoped)
    bot_profile = await _get_bot_profile(body.bot_profile_id, tenant_id, session)

    # 2. Resolve or create chat session
    if body.chat_id:
        chat_session = await _get_chat(body.chat_id, tenant_id, session)
    else:
        chat_session = Chat(
            tenant_id=tenant_id,
            bot_profile_id=bot_profile.id,
            user_id=auth.user_id,
            title=body.message[:100],
        )
        session.add(chat_session)
        await session.flush()

    # 3. Load conversation history (before saving new message)
    history = await _load_history(chat_session.id, session)

    # 4. Save user message
    user_msg = Message(
        tenant_id=tenant_id,
        chat_id=chat_session.id,
        role=MessageRole.USER,
        content=body.message,
    )
    session.add(user_msg)
    await session.flush()

    # 5. Decrypt provider credentials if set
    api_key = None
    if bot_profile.encrypted_credentials:
        creds = json.loads(decrypt_value(bot_profile.encrypted_credentials))
        api_key = creds.get("api_key")

    # 6. Run the RAG orchestrator
    result = await run_chat_turn(
        user_message=body.message,
        bot_profile=bot_profile,
        tenant_id=str(tenant_id),
        history=history,
        api_key=api_key,
    )

    # 7. Save assistant message
    chunk_ids = [c.chunk_id for c in result.retrieved_chunks]
    assistant_msg = Message(
        tenant_id=tenant_id,
        chat_id=chat_session.id,
        role=MessageRole.ASSISTANT,
        content=result.content,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        context_chunks=json.dumps(chunk_ids),
    )
    session.add(assistant_msg)
    await session.flush()

    # 8. Record usage event
    usage_event = UsageEvent(
        tenant_id=tenant_id,
        chat_id=chat_session.id,
        message_id=assistant_msg.id,
        bot_profile_id=bot_profile.id,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
    )
    session.add(usage_event)

    # 9. Update chat counters
    chat_session.message_count += 2  # user + assistant
    chat_session.total_prompt_tokens += result.prompt_tokens
    chat_session.total_completion_tokens += result.completion_tokens
    session.add(chat_session)

    await session.commit()
    await session.refresh(assistant_msg)

    # 10. Build response
    sources = [
        {
            "chunk_id": c.chunk_id,
            "content": c.content[:200],
            "score": round(c.score, 4),
            "source_id": c.source_id,
        }
        for c in result.retrieved_chunks
    ]

    return ChatMessageResponse(
        chat_id=chat_session.id,
        message=MessageRead(
            id=assistant_msg.id,
            chat_id=chat_session.id,
            role=assistant_msg.role,
            content=assistant_msg.content,
            prompt_tokens=assistant_msg.prompt_tokens,
            completion_tokens=assistant_msg.completion_tokens,
            created_at=assistant_msg.created_at,
        ),
        sources=sources,
        usage={
            "model": result.model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        },
    )


# ── Chat history endpoint ────────────────────────────────────

@router.get("/{chat_id}", response_model=ChatRead)
async def get_chat(
    chat_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> ChatRead:
    chat_session = await _get_chat(chat_id, auth.tenant_id, session)
    return ChatRead.model_validate(chat_session)


@router.get("/{chat_id}/messages", response_model=list[MessageRead])
async def get_chat_messages(
    chat_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> list[MessageRead]:
    await _get_chat(chat_id, auth.tenant_id, session)  # verify access

    stmt = (
        select(Message)
        .where(Message.chat_id == chat_id, Message.tenant_id == auth.tenant_id)
        .order_by(Message.created_at.asc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    return [MessageRead.model_validate(m) for m in result.scalars().all()]


# ── Internal helpers ──────────────────────────────────────────

async def _get_bot_profile(
    profile_id: uuid.UUID, tenant_id: uuid.UUID, session
) -> BotProfile:
    stmt = select(BotProfile).where(
        BotProfile.id == profile_id,
        BotProfile.tenant_id == tenant_id,
        BotProfile.is_active.is_(True),  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    bp = result.scalar_one_or_none()
    if bp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot profile not found or inactive",
        )
    return bp


async def _get_chat(
    chat_id: uuid.UUID, tenant_id: uuid.UUID, session
) -> Chat:
    stmt = select(Chat).where(
        Chat.id == chat_id,
        Chat.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )
    return chat


async def _load_history(chat_id: uuid.UUID, session) -> list[dict]:
    """Load previous messages for context (user + assistant only)."""
    stmt = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),  # type: ignore[union-attr]
        )
        .order_by(Message.created_at.asc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    messages = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in messages]
