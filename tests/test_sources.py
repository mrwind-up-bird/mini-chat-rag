"""Source CRUD tests."""


import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + create a bot profile, return headers + profile_id."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Src Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}

    resp = await client.post("/v1/bot-profiles", json={"name": "Bot"}, headers=headers)
    assert resp.status_code == 201
    profile_id = resp.json()["id"]

    return {"headers": headers, "profile_id": profile_id, "tenant": data["tenant"]}


@pytest.mark.asyncio
async def test_create_and_get_source(client: AsyncClient):
    ctx = await _setup(client, "src-create")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "FAQ",
        "source_type": "text",
        "content": "What is MiniRAG? It is a RAG platform.",
    }, headers=ctx["headers"])
    assert resp.status_code == 201
    src = resp.json()
    assert src["name"] == "FAQ"
    assert src["source_type"] == "text"
    assert src["status"] == "pending"

    # Get by ID
    resp = await client.get(f"/v1/sources/{src['id']}", headers=ctx["headers"])
    assert resp.status_code == 200
    assert resp.json()["name"] == "FAQ"


@pytest.mark.asyncio
async def test_list_sources_with_filter(client: AsyncClient):
    ctx = await _setup(client, "src-list")
    headers = ctx["headers"]

    # Create another bot profile
    resp = await client.post("/v1/bot-profiles", json={"name": "Bot 2"}, headers=headers)
    other_id = resp.json()["id"]

    await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Source A",
        "source_type": "text",
    }, headers=headers)
    await client.post("/v1/sources", json={
        "bot_profile_id": other_id,
        "name": "Source B",
        "source_type": "url",
        "config": {"url": "https://example.com"},
    }, headers=headers)

    # List all
    resp = await client.get("/v1/sources", headers=headers)
    assert len(resp.json()) == 2

    # Filter by bot_profile_id
    resp = await client.get(f"/v1/sources?bot_profile_id={ctx['profile_id']}", headers=headers)
    sources = resp.json()
    assert len(sources) == 1
    assert sources[0]["name"] == "Source A"


@pytest.mark.asyncio
async def test_update_source(client: AsyncClient):
    ctx = await _setup(client, "src-update")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Old",
        "source_type": "text",
    }, headers=ctx["headers"])
    src_id = resp.json()["id"]

    resp = await client.patch(f"/v1/sources/{src_id}", json={
        "name": "Renamed",
        "content": "Updated content",
    }, headers=ctx["headers"])
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_source(client: AsyncClient):
    ctx = await _setup(client, "src-delete")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Gone",
        "source_type": "text",
    }, headers=ctx["headers"])
    src_id = resp.json()["id"]

    resp = await client.delete(f"/v1/sources/{src_id}", headers=ctx["headers"])
    assert resp.status_code == 204

    resp = await client.get(f"/v1/sources/{src_id}", headers=ctx["headers"])
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_cross_tenant_bot_profile_rejected(client: AsyncClient):
    """Creating a source with another tenant's bot_profile_id fails."""
    ctx_a = await _setup(client, "src-cross-a")
    ctx_b = await _setup(client, "src-cross-b")

    # Try to create source in tenant B using tenant A's bot_profile_id
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx_a["profile_id"],
        "name": "Evil",
        "source_type": "text",
    }, headers=ctx_b["headers"])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tenant_isolation_sources(client: AsyncClient):
    ctx_a = await _setup(client, "src-iso-a")
    ctx_b = await _setup(client, "src-iso-b")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx_a["profile_id"],
        "name": "Private",
        "source_type": "text",
    }, headers=ctx_a["headers"])
    src_id = resp.json()["id"]

    # Tenant B cannot see tenant A's source
    resp = await client.get(f"/v1/sources/{src_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404
