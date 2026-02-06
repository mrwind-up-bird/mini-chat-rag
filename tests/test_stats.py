"""Tests for stats endpoints."""

import pytest
from httpx import AsyncClient


async def _bootstrap(client: AsyncClient, slug: str):
    resp = await client.post("/v1/tenants", json={
        "tenant_name": f"{slug} Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    data = resp.json()
    return {"Authorization": f"Bearer {data['api_token']}"}


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
