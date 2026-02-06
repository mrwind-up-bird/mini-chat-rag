"""Tests for system health endpoint."""

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
async def test_system_health_returns_structure(client: AsyncClient):
    """GET /v1/system/health returns status for all services."""
    headers = await _bootstrap(client, slug="health-struct")

    resp = await client.get("/v1/system/health", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "postgres" in data
    assert "qdrant" in data
    assert "redis" in data
    # Postgres should be ok (using SQLite in tests)
    assert data["postgres"]["status"] == "ok"


@pytest.mark.asyncio
async def test_system_health_qdrant_redis_degrade_gracefully(client: AsyncClient):
    """Qdrant and Redis report error in test env (not running)."""
    headers = await _bootstrap(client, slug="health-degrade")

    resp = await client.get("/v1/system/health", headers=headers)
    data = resp.json()
    # In test env without Qdrant/Redis running, these should be "error"
    assert data["qdrant"]["status"] in ("ok", "error")
    assert data["redis"]["status"] in ("ok", "error")
    # Overall should reflect degraded if any service is down
    if data["qdrant"]["status"] == "error" or data["redis"]["status"] == "error":
        assert data["status"] == "degraded"
