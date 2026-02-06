"""Tests for message feedback endpoint (PATCH /v1/chat/{chat_id}/messages/{message_id}/feedback)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


async def _setup_chat_with_messages(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + bot profile + one chat with a user & assistant message."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Feedback Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}

    resp = await client.post("/v1/bot-profiles", json={
        "name": "FB Bot",
        "system_prompt": "You are helpful.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    profile_id = resp.json()["id"]

    # Create a chat with mocked LLM
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 30

    message = MagicMock()
    message.content = "Sure, I can help!"

    choice = MagicMock()
    choice.message = message

    llm_resp = MagicMock()
    llm_resp.choices = [choice]
    llm_resp.usage = usage

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=[])
    mock_llm = AsyncMock(return_value=llm_resp)

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": profile_id,
            "message": "Help me",
        }, headers=headers)

    chat_data = resp.json()
    chat_id = chat_data["chat_id"]
    assistant_msg_id = chat_data["message"]["id"]

    # Get all messages to find the user message
    resp = await client.get(f"/v1/chat/{chat_id}/messages", headers=headers)
    msgs = resp.json()
    user_msg_id = next(m["id"] for m in msgs if m["role"] == "user")

    return {
        "headers": headers,
        "chat_id": chat_id,
        "assistant_msg_id": assistant_msg_id,
        "user_msg_id": user_msg_id,
    }


@pytest.mark.asyncio
async def test_submit_positive_feedback(client: AsyncClient):
    """Submit positive feedback on an assistant message."""
    ctx = await _setup_chat_with_messages(client, "fb-pos")

    resp = await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['assistant_msg_id']}/feedback",
        json={"feedback": "positive"},
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["feedback"] == "positive"


@pytest.mark.asyncio
async def test_toggle_feedback_to_negative(client: AsyncClient):
    """Toggle feedback from positive to negative."""
    ctx = await _setup_chat_with_messages(client, "fb-toggle")

    # Set positive
    await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['assistant_msg_id']}/feedback",
        json={"feedback": "positive"},
        headers=ctx["headers"],
    )

    # Toggle to negative
    resp = await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['assistant_msg_id']}/feedback",
        json={"feedback": "negative"},
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["feedback"] == "negative"


@pytest.mark.asyncio
async def test_clear_feedback(client: AsyncClient):
    """Clear feedback by sending null."""
    ctx = await _setup_chat_with_messages(client, "fb-clear")

    # Set positive first
    await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['assistant_msg_id']}/feedback",
        json={"feedback": "positive"},
        headers=ctx["headers"],
    )

    # Clear
    resp = await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['assistant_msg_id']}/feedback",
        json={"feedback": None},
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["feedback"] is None


@pytest.mark.asyncio
async def test_feedback_rejected_on_user_message(client: AsyncClient):
    """Feedback on a user message returns 422."""
    ctx = await _setup_chat_with_messages(client, "fb-reject")

    resp = await client.patch(
        f"/v1/chat/{ctx['chat_id']}/messages/{ctx['user_msg_id']}/feedback",
        json={"feedback": "positive"},
        headers=ctx["headers"],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_feedback_cross_tenant_isolation(client: AsyncClient):
    """Tenant B cannot submit feedback on Tenant A's messages."""
    ctx_a = await _setup_chat_with_messages(client, "fb-iso-a")

    # Create tenant B
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Other Co",
        "tenant_slug": "fb-iso-b",
        "owner_email": "fb-iso-b@test.com",
        "owner_password": "password1234",
    })
    headers_b = {"Authorization": f"Bearer {resp.json()['api_token']}"}

    resp = await client.patch(
        f"/v1/chat/{ctx_a['chat_id']}/messages/{ctx_a['assistant_msg_id']}/feedback",
        json={"feedback": "positive"},
        headers=headers_b,
    )
    assert resp.status_code == 404
