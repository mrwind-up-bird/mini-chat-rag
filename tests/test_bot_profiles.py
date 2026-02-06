"""BotProfile CRUD tests."""

import pytest
from httpx import AsyncClient


async def _bootstrap(client: AsyncClient, slug: str = "bp-test") -> dict:
    """Helper: create a tenant and return headers + tenant data."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Test Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    return {"headers": headers, "tenant": data["tenant"]}


@pytest.mark.asyncio
async def test_create_and_get_bot_profile(client: AsyncClient):
    ctx = await _bootstrap(client, "bp-create")
    headers = ctx["headers"]

    # Create
    resp = await client.post("/v1/bot-profiles", json={
        "name": "Support Bot",
        "system_prompt": "You help customers.",
        "model": "gpt-4o",
        "temperature": 0.3,
    }, headers=headers)
    assert resp.status_code == 201
    bp = resp.json()
    assert bp["name"] == "Support Bot"
    assert bp["model"] == "gpt-4o"
    assert bp["temperature"] == 0.3
    assert bp["has_credentials"] is False

    # Get by ID
    resp = await client.get(f"/v1/bot-profiles/{bp['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Support Bot"


@pytest.mark.asyncio
async def test_list_bot_profiles(client: AsyncClient):
    ctx = await _bootstrap(client, "bp-list")
    headers = ctx["headers"]

    await client.post("/v1/bot-profiles", json={"name": "Bot A"}, headers=headers)
    await client.post("/v1/bot-profiles", json={"name": "Bot B"}, headers=headers)

    resp = await client.get("/v1/bot-profiles", headers=headers)
    assert resp.status_code == 200
    profiles = resp.json()
    assert len(profiles) == 2


@pytest.mark.asyncio
async def test_update_bot_profile(client: AsyncClient):
    ctx = await _bootstrap(client, "bp-update")
    headers = ctx["headers"]

    resp = await client.post("/v1/bot-profiles", json={"name": "Old Name"}, headers=headers)
    bp_id = resp.json()["id"]

    resp = await client.patch(f"/v1/bot-profiles/{bp_id}", json={
        "name": "New Name",
        "temperature": 1.5,
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["temperature"] == 1.5


@pytest.mark.asyncio
async def test_delete_bot_profile(client: AsyncClient):
    ctx = await _bootstrap(client, "bp-delete")
    headers = ctx["headers"]

    resp = await client.post("/v1/bot-profiles", json={"name": "Doomed"}, headers=headers)
    bp_id = resp.json()["id"]

    resp = await client.delete(f"/v1/bot-profiles/{bp_id}", headers=headers)
    assert resp.status_code == 204

    # Still accessible but inactive
    resp = await client.get(f"/v1/bot-profiles/{bp_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_tenant_isolation_bot_profiles(client: AsyncClient):
    """Tenant A cannot see Tenant B's bot profiles."""
    ctx_a = await _bootstrap(client, "bp-iso-a")
    ctx_b = await _bootstrap(client, "bp-iso-b")

    # Create profile in tenant A
    resp = await client.post(
        "/v1/bot-profiles", json={"name": "A's Bot"}, headers=ctx_a["headers"]
    )
    bp_id = resp.json()["id"]

    # Tenant B cannot see it
    resp = await client.get(f"/v1/bot-profiles/{bp_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404

    # Tenant B's list is empty
    resp = await client.get("/v1/bot-profiles", headers=ctx_b["headers"])
    assert resp.json() == []
