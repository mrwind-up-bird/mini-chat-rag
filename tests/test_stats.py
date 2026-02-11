"""Tests for stats endpoints."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.models.message import Message, MessageRole
from app.models.usage_event import UsageEvent


async def _bootstrap(client: AsyncClient, slug: str):
    resp = await client.post("/v1/tenants", json={
        "tenant_name": f"{slug} Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    data = resp.json()
    return {"Authorization": f"Bearer {data['api_token']}"}


async def _bootstrap_with_usage(client: AsyncClient, session: AsyncSession, slug: str):
    """Bootstrap a tenant, create a bot, and insert usage events directly."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": f"{slug} Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = uuid.UUID(data["tenant"]["id"])

    # Get user_id from auth/me
    me_resp = await client.get("/v1/auth/me", headers=headers)
    user_id = uuid.UUID(me_resp.json()["user"]["id"])

    # Create bot profile via API
    bp_resp = await client.post("/v1/bot-profiles", json={
        "name": "Cost Bot",
        "model": "gpt-4o-mini",
    }, headers=headers)
    bp_id = uuid.UUID(bp_resp.json()["id"])

    # Create chat + message + usage event directly in DB
    chat = Chat(
        tenant_id=tenant_id,
        bot_profile_id=bp_id,
        user_id=user_id,
        title="Test chat",
        message_count=2,
        total_prompt_tokens=500,
        total_completion_tokens=200,
    )
    session.add(chat)
    await session.flush()

    msg = Message(
        tenant_id=tenant_id,
        chat_id=chat.id,
        role=MessageRole.ASSISTANT,
        content="Hello",
        prompt_tokens=500,
        completion_tokens=200,
    )
    session.add(msg)
    await session.flush()

    usage = UsageEvent(
        tenant_id=tenant_id,
        chat_id=chat.id,
        message_id=msg.id,
        bot_profile_id=bp_id,
        model="gpt-4o-mini",
        prompt_tokens=500,
        completion_tokens=200,
        total_tokens=700,
    )
    session.add(usage)
    await session.commit()

    return headers, tenant_id, bp_id


@pytest.mark.asyncio
async def test_overview_stats(client: AsyncClient):
    """GET /v1/stats/overview returns summary counts."""
    headers = await _bootstrap(client, slug="stats-overview")

    resp = await client.get("/v1/stats/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "bot_profiles" in data
    assert "sources" in data
    assert "chats" in data
    assert "total_tokens" in data
    assert data["total_tokens"] >= 0


@pytest.mark.asyncio
async def test_overview_counts_bot_profiles(client: AsyncClient):
    """Overview counts should reflect created resources."""
    headers = await _bootstrap(client, slug="stats-count")

    # Create a bot profile
    await client.post("/v1/bot-profiles", json={
        "name": "Counter Bot",
        "model": "gpt-4o-mini",
    }, headers=headers)

    resp = await client.get("/v1/stats/overview", headers=headers)
    assert resp.json()["bot_profiles"] >= 1


@pytest.mark.asyncio
async def test_usage_stats_empty(client: AsyncClient):
    """GET /v1/stats/usage returns empty list when no usage."""
    headers = await _bootstrap(client, slug="stats-usage")

    resp = await client.get("/v1/stats/usage", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_stats_requires_auth(client: AsyncClient):
    """Stats endpoints require authentication."""
    resp = await client.get("/v1/stats/overview")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_usage_by_model_empty(client: AsyncClient):
    """GET /v1/stats/usage/by-model returns empty list when no usage."""
    headers = await _bootstrap(client, slug="stats-bymodel-empty")

    resp = await client.get("/v1/stats/usage/by-model", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_usage_by_bot_empty(client: AsyncClient):
    """GET /v1/stats/usage/by-bot returns empty list when no usage."""
    headers = await _bootstrap(client, slug="stats-bybot-empty")

    resp = await client.get("/v1/stats/usage/by-bot", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_cost_estimate_empty(client: AsyncClient):
    """GET /v1/stats/cost-estimate returns zeros when no usage."""
    headers = await _bootstrap(client, slug="stats-cost-empty")

    resp = await client.get("/v1/stats/cost-estimate", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cost_usd"] == 0
    assert data["daily_avg_cost_usd"] == 0
    assert data["projected_monthly_usd"] == 0
    assert data["active_days"] == 0
    assert data["by_model"] == []
    assert data["by_bot"] == []


@pytest.mark.asyncio
async def test_usage_by_model_with_data(client: AsyncClient, session: AsyncSession):
    """GET /v1/stats/usage/by-model returns model breakdown with cost."""
    headers, _, _ = await _bootstrap_with_usage(client, session, slug="stats-bymodel")

    resp = await client.get("/v1/stats/usage/by-model", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["model"] == "gpt-4o-mini"
    assert row["prompt_tokens"] == 500
    assert row["completion_tokens"] == 200
    assert row["total_tokens"] == 700
    assert row["request_count"] == 1
    assert row["cost_usd"] > 0
    # gpt-4o-mini: $0.15/1M prompt + $0.60/1M completion
    # cost = (500 * 0.15 + 200 * 0.60) / 1_000_000
    expected_cost = (500 * 0.15 + 200 * 0.60) / 1_000_000
    assert abs(row["cost_usd"] - expected_cost) < 0.000001
    assert row["prompt_cost_per_1m"] == 0.15
    assert row["completion_cost_per_1m"] == 0.60


@pytest.mark.asyncio
async def test_usage_by_bot_with_data(client: AsyncClient, session: AsyncSession):
    """GET /v1/stats/usage/by-bot returns per-bot breakdown."""
    headers, _, bp_id = await _bootstrap_with_usage(client, session, slug="stats-bybot")

    resp = await client.get("/v1/stats/usage/by-bot", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["bot_profile_id"] == str(bp_id)
    assert row["bot_name"] == "Cost Bot"
    assert row["model"] == "gpt-4o-mini"
    assert row["total_tokens"] == 700
    assert row["cost_usd"] > 0


@pytest.mark.asyncio
async def test_cost_estimate_with_data(client: AsyncClient, session: AsyncSession):
    """GET /v1/stats/cost-estimate returns cost summary and projections."""
    headers, _, _ = await _bootstrap_with_usage(client, session, slug="stats-cost")

    resp = await client.get("/v1/stats/cost-estimate", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cost_usd"] > 0
    assert data["active_days"] >= 1
    assert data["daily_avg_cost_usd"] > 0
    assert data["projected_monthly_usd"] > 0
    assert len(data["by_model"]) == 1
    assert len(data["by_bot"]) == 1
    assert data["by_model"][0]["model"] == "gpt-4o-mini"
    assert data["by_bot"][0]["bot_name"] == "Cost Bot"


@pytest.mark.asyncio
async def test_cost_estimate_custom_days(client: AsyncClient, session: AsyncSession):
    """GET /v1/stats/cost-estimate?days=7 accepts custom window."""
    headers, _, _ = await _bootstrap_with_usage(client, session, slug="stats-cost-days")

    resp = await client.get("/v1/stats/cost-estimate?days=7", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Data was just created so should appear in 7-day window too
    assert data["total_cost_usd"] > 0


@pytest.mark.asyncio
async def test_days_filter_on_all_endpoints(client: AsyncClient, session: AsyncSession):
    """All usage endpoints accept ?days= and return filtered data."""
    headers, _, _ = await _bootstrap_with_usage(client, session, slug="stats-days-filter")

    for path in ["/v1/stats/usage", "/v1/stats/usage/by-model", "/v1/stats/usage/by-bot"]:
        resp = await client.get(f"{path}?days=7", headers=headers)
        assert resp.status_code == 200, f"{path}?days=7 failed"
        assert len(resp.json()) >= 1, f"{path}?days=7 should include recent data"


@pytest.mark.asyncio
async def test_pricing_endpoint(client: AsyncClient):
    """GET /v1/stats/pricing returns the model pricing map."""
    headers = await _bootstrap(client, slug="stats-pricing")

    resp = await client.get("/v1/stats/pricing", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "default" in data
    # Verify a known model is present with correct structure
    assert "gpt-4o-mini" in data["models"]
    gpt4o_mini = data["models"]["gpt-4o-mini"]
    assert gpt4o_mini["prompt_cost_per_1m"] == 0.15
    assert gpt4o_mini["completion_cost_per_1m"] == 0.60
    # Verify default fallback is present
    assert data["default"]["prompt_cost_per_1m"] > 0
    assert data["default"]["completion_cost_per_1m"] > 0


@pytest.mark.asyncio
async def test_stats_caching(client: AsyncClient, session: AsyncSession):
    """Stats endpoints return cached data on repeated calls."""
    headers, _, _ = await _bootstrap_with_usage(client, session, slug="stats-cache")

    # First call populates cache
    resp1 = await client.get("/v1/stats/overview", headers=headers)
    assert resp1.status_code == 200

    # Second call should return identical data (from cache)
    resp2 = await client.get("/v1/stats/overview", headers=headers)
    assert resp2.status_code == 200
    assert resp1.json() == resp2.json()


@pytest.mark.asyncio
async def test_new_stats_require_auth(client: AsyncClient):
    """New stats endpoints require authentication."""
    for path in ["/v1/stats/usage/by-model", "/v1/stats/usage/by-bot", "/v1/stats/cost-estimate", "/v1/stats/pricing"]:
        resp = await client.get(path)
        assert resp.status_code in (401, 403), f"{path} should require auth"
