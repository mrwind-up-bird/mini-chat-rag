"""Chat endpoint tests — full RAG flow with mocked LLM + vector search."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


async def _setup_chat(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + bot profile, return headers + IDs."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Chat Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    resp = await client.post("/v1/bot-profiles", json={
        "name": "Support Bot",
        "system_prompt": "You are a helpful support assistant.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    profile_id = resp.json()["id"]

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
    }


def _mock_llm_response(content: str = "This is the assistant's response."):
    """Create a mock LiteLLM acompletion response."""
    usage = MagicMock()
    usage.prompt_tokens = 150
    usage.completion_tokens = 42

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage

    return response


def _mock_search_results():
    """Return fake Qdrant search results."""
    return [
        {
            "id": "chunk-001",
            "score": 0.92,
            "payload": {
                "content": "MiniRAG is a modular RAG platform.",
                "source_id": "source-001",
                "tenant_id": "t1",
                "bot_profile_id": "bp1",
            },
        },
        {
            "id": "chunk-002",
            "score": 0.85,
            "payload": {
                "content": "It supports multi-tenancy and provides isolation.",
                "source_id": "source-001",
                "tenant_id": "t1",
                "bot_profile_id": "bp1",
            },
        },
    ]


@pytest.mark.asyncio
async def test_chat_new_conversation(client: AsyncClient):
    """Send a message without chat_id — creates a new conversation."""
    ctx = await _setup_chat(client, "chat-new")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=_mock_search_results())
    mock_llm = AsyncMock(return_value=_mock_llm_response())

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "What is MiniRAG?",
        }, headers=ctx["headers"])

    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Verify response structure
    assert "chat_id" in data
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content"] == "This is the assistant's response."
    assert data["message"]["prompt_tokens"] == 150
    assert data["message"]["completion_tokens"] == 42

    # Verify sources returned
    assert len(data["sources"]) == 2
    assert data["sources"][0]["score"] == 0.92

    # Verify usage
    assert data["usage"]["model"] == "gpt-4o-mini"
    assert data["usage"]["total_tokens"] == 192


@pytest.mark.asyncio
async def test_chat_continue_conversation(client: AsyncClient):
    """Send a follow-up message to an existing chat session."""
    ctx = await _setup_chat(client, "chat-continue")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=_mock_search_results())
    mock_llm = AsyncMock(return_value=_mock_llm_response("First response"))

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp1 = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Tell me about MiniRAG",
        }, headers=ctx["headers"])

    assert resp1.status_code == 200
    chat_id = resp1.json()["chat_id"]

    # Follow-up with existing chat_id
    mock_llm_2 = AsyncMock(return_value=_mock_llm_response("Follow-up response"))
    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm_2),
    ):
        resp2 = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Tell me more",
            "chat_id": chat_id,
        }, headers=ctx["headers"])

    assert resp2.status_code == 200
    assert resp2.json()["chat_id"] == chat_id
    assert resp2.json()["message"]["content"] == "Follow-up response"

    # Verify the LLM received conversation history
    call_args = mock_llm_2.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
    # Should have: system, user1, assistant1, user2
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 2


@pytest.mark.asyncio
async def test_chat_get_history(client: AsyncClient):
    """Retrieve chat metadata and messages."""
    ctx = await _setup_chat(client, "chat-history")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm = AsyncMock(return_value=_mock_llm_response())

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Hello",
        }, headers=ctx["headers"])

    chat_id = resp.json()["chat_id"]

    # Get chat metadata
    resp = await client.get(f"/v1/chat/{chat_id}", headers=ctx["headers"])
    assert resp.status_code == 200
    chat_data = resp.json()
    assert chat_data["message_count"] == 2  # user + assistant
    assert chat_data["total_prompt_tokens"] == 150
    assert chat_data["total_completion_tokens"] == 42

    # Get messages
    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=ctx["headers"])
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_chat_invalid_bot_profile(client: AsyncClient):
    """Chatting with a nonexistent bot_profile_id returns 404."""
    ctx = await _setup_chat(client, "chat-invalid-bp")

    import uuid
    resp = await client.post("/v1/chat", json={
        "bot_profile_id": str(uuid.uuid4()),
        "message": "Hello",
    }, headers=ctx["headers"])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_tenant_isolation(client: AsyncClient):
    """Tenant A cannot access Tenant B's chat session."""
    ctx_a = await _setup_chat(client, "chat-iso-a")
    ctx_b = await _setup_chat(client, "chat-iso-b")

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm = AsyncMock(return_value=_mock_llm_response())

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx_a["profile_id"],
            "message": "Secret question",
        }, headers=ctx_a["headers"])

    chat_id = resp.json()["chat_id"]

    # Tenant B cannot see Tenant A's chat
    resp = await client.get(f"/v1/chat/{chat_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404

    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=ctx_b["headers"])
    assert resp.status_code == 404
