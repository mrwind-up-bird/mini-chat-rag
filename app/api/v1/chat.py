"""Chat endpoint — the main RAG interaction point."""

import csv
import io
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import decrypt_value
from app.models.bot_profile import BotProfile
from app.models.chat import Chat, ChatRead
from app.models.message import Message, MessageRead, MessageRole
from app.models.usage_event import UsageEvent
from app.services.orchestrator import (
    ChatResponse,
    StreamEvent,
    run_chat_turn,
    run_chat_turn_stream,
)

logger = logging.getLogger(__name__)

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


# ── Bulk export (must be before /{chat_id} routes) ────────────

@router.get("/export")
async def export_chats(
    auth: Auth,
    session: Session,
    bot_profile_id: uuid.UUID | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    format: str = "json",
    limit: int = 100,
):
    """Export multiple chat sessions with messages."""
    limit = min(limit, 1000)

    stmt = select(Chat).where(Chat.tenant_id == auth.tenant_id)
    if bot_profile_id:
        stmt = stmt.where(Chat.bot_profile_id == bot_profile_id)
    if from_date:
        parsed_from = date.fromisoformat(from_date)
        stmt = stmt.where(Chat.created_at >= parsed_from.isoformat())
    if to_date:
        parsed_to = date.fromisoformat(to_date)
        # Include the full to_date day
        stmt = stmt.where(Chat.created_at < (parsed_to.isoformat() + "T23:59:59.999999"))
    stmt = stmt.order_by(Chat.created_at.desc()).limit(limit)  # type: ignore[union-attr]

    result = await session.execute(stmt)
    chats = result.scalars().all()

    # Load messages for each chat
    export_data = []
    for chat_obj in chats:
        msg_stmt = (
            select(Message)
            .where(Message.chat_id == chat_obj.id, Message.tenant_id == auth.tenant_id)
            .order_by(Message.created_at.asc())  # type: ignore[union-attr]
        )
        msg_result = await session.execute(msg_stmt)
        messages = msg_result.scalars().all()
        export_data.append({
            "chat": ChatRead.model_validate(chat_obj),
            "messages": [MessageRead.model_validate(m) for m in messages],
        })

    if format == "csv":
        return _bulk_export_csv(export_data)

    from app.models.base import utcnow

    return {
        "chats": [
            {
                "chat": item["chat"].model_dump(mode="json"),
                "messages": [m.model_dump(mode="json") for m in item["messages"]],
            }
            for item in export_data
        ],
        "exported_at": utcnow().isoformat(),
    }


def _bulk_export_csv(export_data: list[dict]) -> StreamingResponse:
    """Build a CSV StreamingResponse for bulk export."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "chat_id", "chat_title", "message_id", "role", "content",
        "feedback", "prompt_tokens", "completion_tokens", "created_at",
    ])
    for item in export_data:
        chat = item["chat"]
        for msg in item["messages"]:
            writer.writerow([
                str(chat.id), chat.title, str(msg.id), msg.role, msg.content,
                msg.feedback or "", msg.prompt_tokens, msg.completion_tokens,
                msg.created_at.isoformat(),
            ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chats_export.csv"},
    )


# ── Request / Response schemas ────────────────────────────────

class ChatRequest(BaseModel):
    bot_profile_id: uuid.UUID
    message: str = Field(min_length=1, max_length=32000)
    chat_id: uuid.UUID | None = Field(
        default=None,
        description="Existing chat session ID. Omit to start a new conversation.",
    )
    stream: bool = Field(
        default=False,
        description="If true, response is streamed as Server-Sent Events.",
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
):
    """Send a message and get a RAG-augmented response.

    Creates a new chat session if chat_id is not provided.
    Retrieves relevant context from the knowledge base, then generates
    a response using the configured LLM.

    When ``stream=true``, returns ``text/event-stream`` with delta/sources/done
    events. Otherwise returns the complete ChatMessageResponse JSON.
    """
    # ── Shared setup (runs before streaming starts) ──────────
    tenant_id = auth.tenant_id
    bot_profile = await _get_bot_profile(body.bot_profile_id, tenant_id, session)

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

    history = await _load_history(chat_session.id, session)

    user_msg = Message(
        tenant_id=tenant_id,
        chat_id=chat_session.id,
        role=MessageRole.USER,
        content=body.message,
    )
    session.add(user_msg)
    await session.flush()

    api_key = None
    if bot_profile.encrypted_credentials:
        creds = json.loads(decrypt_value(bot_profile.encrypted_credentials))
        api_key = creds.get("api_key")

    # ── Streaming path ───────────────────────────────────────
    if body.stream:
        return StreamingResponse(
            _stream_chat_sse(
                body=body,
                tenant_id=tenant_id,
                bot_profile=bot_profile,
                chat_session=chat_session,
                history=history,
                api_key=api_key,
                session=session,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Non-streaming path (unchanged) ───────────────────────
    result = await run_chat_turn(
        user_message=body.message,
        bot_profile=bot_profile,
        tenant_id=str(tenant_id),
        history=history,
        api_key=api_key,
    )

    assistant_msg = await _persist_assistant_message(
        session=session,
        tenant_id=tenant_id,
        chat_session=chat_session,
        bot_profile=bot_profile,
        result=result,
        is_stream=False,
    )

    # Dispatch webhook (fire-and-forget, don't block response)
    try:
        from app.services.webhook_dispatch import dispatch_webhook_event

        await dispatch_webhook_event(session, str(tenant_id), "chat.message", {
            "chat_id": str(chat_session.id),
            "message_id": str(assistant_msg.id),
            "bot_profile_id": str(bot_profile.id),
        })
    except Exception:
        logger.exception("Webhook dispatch failed for chat message")

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
            feedback=assistant_msg.feedback,
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


# ── SSE streaming generator ──────────────────────────────────

def _format_sse(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_chat_sse(
    body: ChatRequest,
    tenant_id: uuid.UUID,
    bot_profile: BotProfile,
    chat_session: Chat,
    history: list[dict],
    api_key: str | None,
    session,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events from the orchestrator."""
    result: ChatResponse | None = None
    try:
        async for item in run_chat_turn_stream(
            user_message=body.message,
            bot_profile=bot_profile,
            tenant_id=str(tenant_id),
            history=history,
            api_key=api_key,
        ):
            if isinstance(item, StreamEvent):
                yield _format_sse(item.event, item.data)
            elif isinstance(item, ChatResponse):
                # Final item — accumulated result for persistence
                result = item

        # Persist after successful stream completion
        if result:
            assistant_msg = await _persist_assistant_message(
                session=session,
                tenant_id=tenant_id,
                chat_session=chat_session,
                bot_profile=bot_profile,
                result=result,
                is_stream=True,
            )
            # Send done event with IDs (after persistence so message_id is real)
            yield _format_sse("done", {
                "chat_id": str(chat_session.id),
                "message_id": str(assistant_msg.id),
                "usage": {
                    "model": result.model,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "total_tokens": result.total_tokens,
                },
            })

    except Exception:
        logger.exception("Error during streaming chat")
        yield _format_sse("error", {"detail": "An error occurred during generation."})


# ── Persistence helper ────────────────────────────────────────

async def _persist_assistant_message(
    session,
    tenant_id: uuid.UUID,
    chat_session: Chat,
    bot_profile: BotProfile,
    result: ChatResponse,
    is_stream: bool,
) -> Message:
    """Save the assistant message, usage event, and update chat counters."""
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

    usage_event = UsageEvent(
        tenant_id=tenant_id,
        chat_id=chat_session.id,
        message_id=assistant_msg.id,
        bot_profile_id=bot_profile.id,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        is_stream=is_stream,
        time_to_first_token_ms=result.time_to_first_token_ms,
        stream_duration_ms=result.stream_duration_ms,
    )
    session.add(usage_event)

    chat_session.message_count += 2  # user + assistant
    chat_session.total_prompt_tokens += result.prompt_tokens
    chat_session.total_completion_tokens += result.completion_tokens
    session.add(chat_session)

    await session.commit()
    await session.refresh(assistant_msg)
    return assistant_msg


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


# ── Single chat export ────────────────────────────────────

@router.get("/{chat_id}/export")
async def export_chat(
    chat_id: uuid.UUID,
    auth: Auth,
    session: Session,
    format: str = "json",
):
    """Export a single chat session with all messages."""
    chat_session = await _get_chat(chat_id, auth.tenant_id, session)

    stmt = (
        select(Message)
        .where(Message.chat_id == chat_id, Message.tenant_id == auth.tenant_id)
        .order_by(Message.created_at.asc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    messages = [MessageRead.model_validate(m) for m in result.scalars().all()]

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "message_id", "role", "content", "feedback",
            "prompt_tokens", "completion_tokens", "created_at",
        ])
        for msg in messages:
            writer.writerow([
                str(msg.id), msg.role, msg.content, msg.feedback or "",
                msg.prompt_tokens, msg.completion_tokens, msg.created_at.isoformat(),
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=chat_{chat_id}.csv",
            },
        )

    from app.models.base import utcnow

    chat_read = ChatRead.model_validate(chat_session)
    return {
        "chat": chat_read.model_dump(mode="json"),
        "messages": [m.model_dump(mode="json") for m in messages],
        "exported_at": utcnow().isoformat(),
    }


# ── Message feedback ──────────────────────────────────────

class FeedbackRequest(BaseModel):
    feedback: Literal["positive", "negative"] | None


@router.patch(
    "/{chat_id}/messages/{message_id}/feedback",
    response_model=MessageRead,
)
async def submit_feedback(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    body: FeedbackRequest,
    auth: Auth,
    session: Session,
) -> MessageRead:
    """Set or clear feedback on an assistant message."""
    await _get_chat(chat_id, auth.tenant_id, session)

    stmt = select(Message).where(
        Message.id == message_id,
        Message.chat_id == chat_id,
        Message.tenant_id == auth.tenant_id,
    )
    result = await session.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    if msg.role != MessageRole.ASSISTANT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Feedback can only be set on assistant messages",
        )

    msg.feedback = body.feedback
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return MessageRead.model_validate(msg)


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
