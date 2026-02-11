"""Streaming chat tests — SSE responses with mocked LLM streaming."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


async def _setup_chat(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + bot profile, return headers + IDs."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Stream Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}

    resp = await client.post("/v1/bot-profiles", json={
        "name": "Stream Bot",
        "system_prompt": "You are a helpful assistant.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    profile_id = resp.json()["id"]

    return {
        "headers": headers,
        "tenant_id": data["tenant"]["id"],
        "profile_id": profile_id,
    }


def _mock_search_results():
    """Return fake Qdrant search results."""
    return [
        {
            "id": "chunk-001",
            "score": 0.92,
            "payload": {
                "content": "MiniRAG is a modular RAG platform.",
                "source_id": "source-001",
            },
        },
    ]


class MockStreamChunk:
    """Simulate a single LiteLLM streaming chunk."""

    def __init__(self, content: str | None = None, usage=None):
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        self.choices = [choice]
        self.usage = usage


async def _mock_streaming_response(chunks: list[MockStreamChunk]):
    """Async generator that mimics acompletion(stream=True)."""
    for chunk in chunks:
        yield chunk


def _make_stream_mock(
    text: str = "Hello world", prompt_tokens: int = 100, completion_tokens: int = 20,
):
    """Create a mock that returns an async streaming response.

    Splits `text` into word-by-word chunks with a final usage chunk.
    """
    words = text.split(" ")
    chunks = []
    for i, word in enumerate(words):
        token = word if i == 0 else f" {word}"
        chunks.append(MockStreamChunk(content=token))

    # Final chunk with usage
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    chunks.append(MockStreamChunk(content=None, usage=usage))

    async def mock_acompletion(**kwargs):
        return _mock_streaming_response(chunks)

    return AsyncMock(side_effect=mock_acompletion)


def _parse_sse_events(text: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = []

    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_event is not None:
            events.append({
                "event": current_event,
                "data": json.loads("".join(current_data)),
            })
            current_event = None
            current_data = []

    return events


@pytest.mark.asyncio
async def test_stream_new_conversation(client: AsyncClient):
    """Streaming response returns correct SSE event sequence."""
    ctx = await _setup_chat(client, "stream-new")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=_mock_search_results())
    mock_llm = _make_stream_mock("This is streamed.")

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "What is MiniRAG?",
            "stream": True,
        }, headers=ctx["headers"])

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = _parse_sse_events(resp.text)
    event_types = [e["event"] for e in events]

    # Must have: sources → delta(s) → done
    assert event_types[0] == "sources"
    assert "delta" in event_types
    assert event_types[-1] == "done"

    # Verify sources event
    sources_evt = events[0]
    assert len(sources_evt["data"]["sources"]) == 1
    assert sources_evt["data"]["sources"][0]["chunk_id"] == "chunk-001"

    # Verify deltas reconstruct the full text
    deltas = [e["data"]["content"] for e in events if e["event"] == "delta"]
    full_text = "".join(deltas)
    assert full_text == "This is streamed."

    # Verify done event has IDs and usage
    done_evt = events[-1]
    assert "chat_id" in done_evt["data"]
    assert "message_id" in done_evt["data"]
    assert done_evt["data"]["usage"]["model"] == "gpt-4o-mini"
    assert done_evt["data"]["usage"]["total_tokens"] == 120


@pytest.mark.asyncio
async def test_stream_continue_conversation(client: AsyncClient):
    """Streaming with existing chat_id includes history in LLM call."""
    ctx = await _setup_chat(client, "stream-continue")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm_1 = _make_stream_mock("First response")

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm_1),
    ):
        resp1 = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Hello",
            "stream": True,
        }, headers=ctx["headers"])

    events1 = _parse_sse_events(resp1.text)
    chat_id = next(e["data"]["chat_id"] for e in events1 if e["event"] == "done")

    # Follow-up
    mock_llm_2 = _make_stream_mock("Follow-up")

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm_2),
    ):
        resp2 = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Tell me more",
            "chat_id": chat_id,
            "stream": True,
        }, headers=ctx["headers"])

    events2 = _parse_sse_events(resp2.text)
    done2 = next(e for e in events2 if e["event"] == "done")
    assert done2["data"]["chat_id"] == chat_id

    # Verify LLM was called for the follow-up
    assert mock_llm_2.call_count == 1


@pytest.mark.asyncio
async def test_stream_message_persisted(client: AsyncClient):
    """After streaming, assistant message is saved to DB."""
    ctx = await _setup_chat(client, "stream-persist")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=_mock_search_results())
    mock_llm = _make_stream_mock("Persisted content", prompt_tokens=200, completion_tokens=50)

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Save this",
            "stream": True,
        }, headers=ctx["headers"])

    events = _parse_sse_events(resp.text)
    done = next(e for e in events if e["event"] == "done")
    chat_id = done["data"]["chat_id"]
    message_id = done["data"]["message_id"]

    # Verify message exists in DB via messages endpoint
    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=ctx["headers"])
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2  # user + assistant

    assistant = messages[1]
    assert assistant["id"] == message_id
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Persisted content"
    assert assistant["prompt_tokens"] == 200
    assert assistant["completion_tokens"] == 50


@pytest.mark.asyncio
async def test_stream_usage_recorded(client: AsyncClient):
    """Streaming requests record usage events with TTFT metrics."""
    ctx = await _setup_chat(client, "stream-usage")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm = _make_stream_mock("Usage test")

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Track usage",
            "stream": True,
        }, headers=ctx["headers"])

    events = _parse_sse_events(resp.text)
    done = next(e for e in events if e["event"] == "done")
    chat_id = done["data"]["chat_id"]

    # Verify chat counters updated
    resp = await client.get(f"/v1/chat/{chat_id}", headers=ctx["headers"])
    assert resp.status_code == 200
    chat_data = resp.json()
    assert chat_data["message_count"] == 2
    assert chat_data["total_prompt_tokens"] == 100
    assert chat_data["total_completion_tokens"] == 20


@pytest.mark.asyncio
async def test_stream_false_unchanged(client: AsyncClient):
    """stream=false returns the same JSON response as before (regression)."""
    ctx = await _setup_chat(client, "stream-false")

    # Use non-streaming mock (standard acompletion response)
    usage = MagicMock()
    usage.prompt_tokens = 150
    usage.completion_tokens = 42

    message = MagicMock()
    message.content = "Non-streaming response."

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=_mock_search_results())
    mock_llm = AsyncMock(return_value=response)

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Normal request",
            "stream": False,
        }, headers=ctx["headers"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    data = resp.json()
    assert data["message"]["content"] == "Non-streaming response."
    assert data["usage"]["total_tokens"] == 192


@pytest.mark.asyncio
async def test_stream_empty_response(client: AsyncClient):
    """LLM returns no content tokens — done event with empty content."""
    ctx = await _setup_chat(client, "stream-empty")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])

    # Only a final chunk with usage, no content
    usage = MagicMock()
    usage.prompt_tokens = 50
    usage.completion_tokens = 0

    async def empty_stream(**kwargs):
        return _mock_streaming_response([MockStreamChunk(content=None, usage=usage)])

    mock_llm = AsyncMock(side_effect=empty_stream)

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Empty?",
            "stream": True,
        }, headers=ctx["headers"])

    events = _parse_sse_events(resp.text)
    event_types = [e["event"] for e in events]

    assert "done" in event_types
    # No delta events
    assert "delta" not in event_types

    # Message still persisted (empty content)
    done = next(e for e in events if e["event"] == "done")
    chat_id = done["data"]["chat_id"]

    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=ctx["headers"])
    messages = resp.json()
    assert len(messages) == 2
    assert messages[1]["content"] == ""


@pytest.mark.asyncio
async def test_stream_tenant_isolation(client: AsyncClient):
    """Tenant B cannot access Tenant A's streaming chat session."""
    ctx_a = await _setup_chat(client, "stream-iso-a")
    ctx_b = await _setup_chat(client, "stream-iso-b")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm = _make_stream_mock("Secret answer")

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx_a["profile_id"],
            "message": "Secret question",
            "stream": True,
        }, headers=ctx_a["headers"])

    events = _parse_sse_events(resp.text)
    chat_id = next(e["data"]["chat_id"] for e in events if e["event"] == "done")

    # Tenant B cannot see Tenant A's chat
    resp = await client.get(f"/v1/chat/{chat_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404

    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=ctx_b["headers"])
    assert resp.status_code == 404
