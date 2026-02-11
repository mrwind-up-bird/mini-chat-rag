"""Chat orchestrator — retrieval-augmented generation pipeline.

Flow:
  1. Embed the user query
  2. Search Qdrant for relevant chunks (scoped by tenant + bot_profile)
  3. Assemble context: system prompt + retrieved chunks + conversation history
  4. Call LLM via LiteLLM (streaming or non-streaming)
  5. Return response + usage statistics
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from litellm import acompletion

from app.models.bot_profile import BotProfile
from app.services.embedding import embed_texts
from app.services.vector_store import search_chunks

logger = logging.getLogger(__name__)

# Maximum context chunks to retrieve
DEFAULT_TOP_K = 5
# Maximum conversation history turns to include
MAX_HISTORY_TURNS = 10


@dataclass
class RetrievedChunk:
    """A chunk retrieved from vector search."""
    chunk_id: str
    content: str
    score: float
    source_id: str


@dataclass
class ChatResponse:
    """The result of an orchestrated chat turn."""
    content: str
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    time_to_first_token_ms: int | None = None
    stream_duration_ms: int | None = None


@dataclass
class StreamEvent:
    """A single event in the streaming response."""
    event: str  # "delta", "sources", "done", "error"
    data: dict = field(default_factory=dict)


async def run_chat_turn(
    user_message: str,
    bot_profile: BotProfile,
    tenant_id: str,
    history: list[dict] | None = None,
    top_k: int = DEFAULT_TOP_K,
    api_key: str | None = None,
) -> ChatResponse:
    """Execute one chat turn through the RAG pipeline.

    Args:
        user_message: The user's latest message.
        bot_profile: The BotProfile config (model, system_prompt, etc.).
        tenant_id: UUID string of the tenant.
        history: Previous messages as [{role, content}, ...].
        top_k: Number of context chunks to retrieve.
        api_key: Optional provider API key (from decrypted bot_profile credentials).

    Returns:
        ChatResponse with the assistant's reply and usage stats.
    """
    # 1. Embed the user query
    query_vectors = await embed_texts([user_message], api_key=api_key)
    query_vector = query_vectors[0] if query_vectors else []

    # 2. Retrieve relevant chunks from Qdrant
    retrieved: list[RetrievedChunk] = []
    if query_vector:
        results = await search_chunks(
            query_vector=query_vector,
            tenant_id=tenant_id,
            bot_profile_id=str(bot_profile.id),
            limit=top_k,
        )
        retrieved = [
            RetrievedChunk(
                chunk_id=r["id"],
                content=r["payload"].get("content", ""),
                score=r["score"],
                source_id=r["payload"].get("source_id", ""),
            )
            for r in results
        ]

    # 3. Assemble the LLM messages
    messages = _build_messages(
        system_prompt=bot_profile.system_prompt,
        retrieved_chunks=retrieved,
        history=history or [],
        user_message=user_message,
    )

    # 4. Call LLM via LiteLLM
    kwargs: dict = {
        "model": bot_profile.model,
        "messages": messages,
        "temperature": bot_profile.temperature,
        "max_tokens": bot_profile.max_tokens,
    }
    if api_key:
        kwargs["api_key"] = api_key

    response = await acompletion(**kwargs)

    # 5. Extract response and usage
    content = response.choices[0].message.content or ""
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0

    return ChatResponse(
        content=content,
        retrieved_chunks=retrieved,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        model=bot_profile.model,
    )


async def run_chat_turn_stream(
    user_message: str,
    bot_profile: BotProfile,
    tenant_id: str,
    history: list[dict] | None = None,
    top_k: int = DEFAULT_TOP_K,
    api_key: str | None = None,
) -> AsyncGenerator[StreamEvent | ChatResponse, None]:
    """Execute a streaming chat turn through the RAG pipeline.

    Yields StreamEvent objects (sources, delta tokens), then finally yields
    a ChatResponse with the complete accumulated result for the route to persist.
    """
    # 1. Embed + retrieve (same as non-streaming)
    query_vectors = await embed_texts([user_message], api_key=api_key)
    query_vector = query_vectors[0] if query_vectors else []

    retrieved: list[RetrievedChunk] = []
    if query_vector:
        results = await search_chunks(
            query_vector=query_vector,
            tenant_id=tenant_id,
            bot_profile_id=str(bot_profile.id),
            limit=top_k,
        )
        retrieved = [
            RetrievedChunk(
                chunk_id=r["id"],
                content=r["payload"].get("content", ""),
                score=r["score"],
                source_id=r["payload"].get("source_id", ""),
            )
            for r in results
        ]

    # 2. Yield sources immediately (before LLM call)
    sources_data = [
        {
            "chunk_id": c.chunk_id,
            "content": c.content[:200],
            "score": round(c.score, 4),
            "source_id": c.source_id,
        }
        for c in retrieved
    ]
    yield StreamEvent(event="sources", data={"sources": sources_data})

    # 3. Build LLM messages
    messages = _build_messages(
        system_prompt=bot_profile.system_prompt,
        retrieved_chunks=retrieved,
        history=history or [],
        user_message=user_message,
    )

    # 4. Call LLM with streaming
    kwargs: dict = {
        "model": bot_profile.model,
        "messages": messages,
        "temperature": bot_profile.temperature,
        "max_tokens": bot_profile.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if api_key:
        kwargs["api_key"] = api_key

    accumulated_content = ""
    prompt_tokens = 0
    completion_tokens = 0
    ttft_ms: int | None = None
    stream_start: float | None = None
    stream_end: float = 0.0
    llm_start = time.monotonic()

    response = await acompletion(**kwargs)
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            now = time.monotonic()
            if ttft_ms is None:
                ttft_ms = int((now - llm_start) * 1000)
                stream_start = now
            stream_end = now
            accumulated_content += delta.content
            yield StreamEvent(event="delta", data={"content": delta.content})

        # Some providers include usage in the final chunk
        if hasattr(chunk, "usage") and chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

    stream_duration = (
        int((stream_end - stream_start) * 1000) if stream_start else None
    )

    total_tokens = prompt_tokens + completion_tokens

    # 5. Yield final ChatResponse for the route to persist + build done event
    yield ChatResponse(
        content=accumulated_content,
        retrieved_chunks=retrieved,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        model=bot_profile.model,
        time_to_first_token_ms=ttft_ms,
        stream_duration_ms=stream_duration,
    )


def _build_messages(
    system_prompt: str,
    retrieved_chunks: list[RetrievedChunk],
    history: list[dict],
    user_message: str,
) -> list[dict]:
    """Assemble the message array for the LLM call."""
    messages: list[dict] = []

    # System prompt with injected context
    context_block = ""
    if retrieved_chunks:
        context_parts = []
        for i, chunk in enumerate(retrieved_chunks, 1):
            context_parts.append(f"[{i}] {chunk.content}")
        context_block = (
            "\n\n---\nRelevant context from the knowledge base:\n"
            + "\n\n".join(context_parts)
            + "\n---\n\nUse the context above to answer the user's question. "
            "If the context doesn't contain relevant information, say so."
        )

    messages.append({
        "role": "system",
        "content": system_prompt + context_block,
    })

    # Conversation history (trim to last N turns)
    trimmed_history = history[-MAX_HISTORY_TURNS * 2 :]  # 2 messages per turn
    for msg in trimmed_history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })

    # Current user message
    messages.append({
        "role": "user",
        "content": user_message,
    })

    return messages
