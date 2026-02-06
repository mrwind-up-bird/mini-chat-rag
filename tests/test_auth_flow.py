"""End-to-end auth flow: bootstrap tenant → use token → manage tokens."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_bootstrap_and_authenticate(client: AsyncClient):
    """Full happy-path: create tenant, use token, list tokens, revoke."""

    # 1. Bootstrap a tenant (unauthenticated)
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Acme Corp",
        "tenant_slug": "acme",
        "owner_email": "admin@acme.com",
        "owner_password": "supersecret123",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["tenant"]["slug"] == "acme"
    raw_token = data["api_token"]
    assert len(raw_token) > 20  # token_urlsafe(32) → ~43 chars

    headers = {"Authorization": f"Bearer {raw_token}"}

    # 2. Use the token to get tenant info
    resp = await client.get("/v1/tenants/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["slug"] == "acme"

    # 3. Create a second token
    resp = await client.post("/v1/api-tokens", json={"name": "ci-bot"}, headers=headers)
    assert resp.status_code == 201
    second = resp.json()
    assert second["name"] == "ci-bot"
    assert "raw_token" in second

    # 4. List tokens — should see 2
    resp = await client.get("/v1/api-tokens", headers=headers)
    assert resp.status_code == 200
    tokens = resp.json()
    assert len(tokens) == 2

    # 5. Revoke the second token
    resp = await client.delete(f"/v1/api-tokens/{second['id']}", headers=headers)
    assert resp.status_code == 204

    # 6. List again — both still returned (soft delete), but second is inactive
    resp = await client.get("/v1/api-tokens", headers=headers)
    tokens = resp.json()
    revoked = [t for t in tokens if t["id"] == second["id"]]
    assert len(revoked) == 1
    assert revoked[0]["is_active"] is False


@pytest.mark.asyncio
async def test_duplicate_slug_rejected(client: AsyncClient):
    """Registering the same slug twice returns 409."""
    payload = {
        "tenant_name": "First",
        "tenant_slug": "unique-slug",
        "owner_email": "a@b.com",
        "owner_password": "password123",
    }
    resp = await client.post("/v1/tenants", json=payload)
    assert resp.status_code == 201

    resp = await client.post("/v1/tenants", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_invalid_token_rejected(client: AsyncClient):
    """A garbage token should return 401."""
    resp = await client.get(
        "/v1/tenants/me",
        headers={"Authorization": "Bearer totally-fake-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_auth_rejected(client: AsyncClient):
    """No Authorization header → 401 or 403 depending on FastAPI version."""
    resp = await client.get("/v1/tenants/me")
    assert resp.status_code in (401, 403)
