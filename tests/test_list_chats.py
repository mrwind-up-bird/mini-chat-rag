"""Tests for GET /v1/chat (list chats) endpoint."""

import pytest
from httpx import AsyncClient


async def _setup_with_chat(client: AsyncClient, slug: str):
    """Bootstrap tenant + bot profile, return headers and profile_id."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": f"{slug} Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}

    # Create a bot profile
    resp = await client.post("/v1/bot-profiles", json={
        "name": "Test Bot",
        "model": "gpt-4o-mini",
        "system_prompt": "You are helpful.",
    }, headers=headers)
    profile_id = resp.json()["id"]

    return headers, profile_id


@pytest.mark.asyncio
async def test_list_chats_empty(client: AsyncClient):
    """List chats returns empty list when no chats exist."""
    headers, _ = await _setup_with_chat(client, slug="chats-empty")

    resp = await client.get("/v1/chat", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_chats_requires_auth(client: AsyncClient):
    """GET /v1/chat without auth returns 401/403."""
    resp = await client.get("/v1/chat")
    assert resp.status_code in (401, 403)
