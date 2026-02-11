"""Tests for conversation export and feedback analytics endpoints."""

import csv
import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.chat import Chat
from app.models.message import Message, MessageRole
from app.models.user import User


async def _bootstrap(client: AsyncClient, slug: str, session: AsyncSession | None = None) -> dict:
    """Bootstrap tenant + bot profile via API, return headers + IDs."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Export Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    # Look up user_id from the DB
    user_id = None
    if session is not None:
        result = await session.execute(
            select(User).where(User.tenant_id == uuid.UUID(tenant_id))
        )
        user = result.scalar_one()
        user_id = str(user.id)

    resp = await client.post("/v1/bot-profiles", json={
        "name": "Export Bot",
        "system_prompt": "You are helpful.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    profile_id = resp.json()["id"]

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": profile_id,
    }


async def _create_chat_with_messages(
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
    bot_profile_id: str,
) -> tuple:
    """Create a chat with messages including feedback, directly via DB."""
    tid = uuid.UUID(tenant_id)
    uid = uuid.UUID(user_id)
    bpid = uuid.UUID(bot_profile_id)

    chat = Chat(
        tenant_id=tid,
        bot_profile_id=bpid,
        user_id=uid,
        title="Test chat",
        message_count=4,
    )
    session.add(chat)
    await session.flush()

    msgs = [
        Message(
            tenant_id=tid, chat_id=chat.id,
            role=MessageRole.USER, content="Hello",
        ),
        Message(
            tenant_id=tid, chat_id=chat.id,
            role=MessageRole.ASSISTANT, content="Hi there!",
            feedback="positive", prompt_tokens=10, completion_tokens=5,
        ),
        Message(
            tenant_id=tid, chat_id=chat.id,
            role=MessageRole.USER, content="Help me",
        ),
        Message(
            tenant_id=tid, chat_id=chat.id,
            role=MessageRole.ASSISTANT, content="Sure!",
            feedback="negative", prompt_tokens=15, completion_tokens=8,
        ),
    ]
    for m in msgs:
        session.add(m)
    await session.flush()
    return chat, msgs


# ── Single chat export ────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_single_chat_json(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "export-single-json", session)
    chat, msgs = await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        f"/v1/chat/{chat.id}/export?format=json",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chat"]["id"] == str(chat.id)
    assert len(data["messages"]) == 4
    assert data["exported_at"] is not None
    # Verify message ordering and content
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["feedback"] == "positive"


@pytest.mark.asyncio
async def test_export_single_chat_csv(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "export-single-csv", session)
    chat, msgs = await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        f"/v1/chat/{chat.id}/export?format=csv",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert f"chat_{chat.id}.csv" in resp.headers["content-disposition"]

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    # Header + 4 messages
    assert len(rows) == 5
    assert rows[0][0] == "message_id"
    assert rows[1][1] == "user"
    assert rows[2][1] == "assistant"


@pytest.mark.asyncio
async def test_export_single_chat_not_found(client: AsyncClient):
    ctx = await _bootstrap(client, "export-single-404")
    fake_id = uuid.uuid4()

    resp = await client.get(
        f"/v1/chat/{fake_id}/export",
        headers=ctx["headers"],
    )
    assert resp.status_code == 404


# ── Bulk export ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_bulk_json(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "export-bulk-json", session)
    chat, _ = await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        "/v1/chat/export?format=json",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["chats"]) >= 1
    assert data["exported_at"] is not None
    # Each chat entry has messages
    found = [c for c in data["chats"] if c["chat"]["id"] == str(chat.id)]
    assert len(found) == 1
    assert len(found[0]["messages"]) == 4


@pytest.mark.asyncio
async def test_export_bulk_csv(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "export-bulk-csv", session)
    chat, _ = await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        "/v1/chat/export?format=csv",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    # Header + at least 4 messages
    assert len(rows) >= 5
    assert rows[0][0] == "chat_id"


@pytest.mark.asyncio
async def test_export_bulk_filter_by_bot(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "export-bulk-filter", session)
    chat, _ = await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    # Filter by the bot profile — should include our chat
    resp = await client.get(
        f"/v1/chat/export?bot_profile_id={ctx['profile_id']}&format=json",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["chats"]) >= 1

    # Filter by a random profile — should be empty
    resp = await client.get(
        f"/v1/chat/export?bot_profile_id={uuid.uuid4()}&format=json",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    assert len(resp.json()["chats"]) == 0


@pytest.mark.asyncio
async def test_export_tenant_isolation(client: AsyncClient, session: AsyncSession):
    ctx_a = await _bootstrap(client, "export-iso-a", session)
    ctx_b = await _bootstrap(client, "export-iso-b", session)

    chat, _ = await _create_chat_with_messages(
        session, ctx_a["tenant_id"], ctx_a["user_id"], ctx_a["profile_id"],
    )

    # Tenant A can export their chat
    resp = await client.get(
        f"/v1/chat/{chat.id}/export",
        headers=ctx_a["headers"],
    )
    assert resp.status_code == 200

    # Tenant B cannot see Tenant A's chat
    resp = await client.get(
        f"/v1/chat/{chat.id}/export",
        headers=ctx_b["headers"],
    )
    assert resp.status_code == 404


# ── Feedback analytics ────────────────────────────────────────


@pytest.mark.asyncio
async def test_feedback_stats(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "feedback-stats", session)
    await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        "/v1/stats/feedback",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_messages"] == 2  # 2 assistant messages
    assert data["positive_count"] == 1
    assert data["negative_count"] == 1
    assert data["total_with_feedback"] == 2
    assert data["feedback_rate"] == 100.0


@pytest.mark.asyncio
async def test_feedback_stats_by_bot(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "feedback-by-bot", session)
    await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        "/v1/stats/feedback",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["by_bot"]) == 1
    bot_stats = data["by_bot"][0]
    assert bot_stats["bot_profile_id"] == ctx["profile_id"]
    assert bot_stats["bot_name"] == "Export Bot"
    assert bot_stats["positive_count"] == 1
    assert bot_stats["negative_count"] == 1
    assert bot_stats["total_messages"] == 2


@pytest.mark.asyncio
async def test_feedback_trend(client: AsyncClient, session: AsyncSession):
    ctx = await _bootstrap(client, "feedback-trend", session)
    await _create_chat_with_messages(
        session, ctx["tenant_id"], ctx["user_id"], ctx["profile_id"],
    )

    resp = await client.get(
        "/v1/stats/feedback/trend",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    point = data[0]
    assert "date" in point
    assert point["positive_count"] >= 1
    assert point["negative_count"] >= 1
    assert point["total_messages"] >= 2


@pytest.mark.asyncio
async def test_feedback_stats_empty(client: AsyncClient):
    ctx = await _bootstrap(client, "feedback-empty")

    resp = await client.get(
        "/v1/stats/feedback",
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_messages"] == 0
    assert data["positive_count"] == 0
    assert data["negative_count"] == 0
    assert data["total_with_feedback"] == 0
    assert data["feedback_rate"] == 0.0
    assert data["by_bot"] == []
