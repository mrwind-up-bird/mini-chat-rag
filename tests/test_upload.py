"""Upload endpoint tests for POST /v1/sources/upload."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + create a bot profile, return headers + profile_id."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Upload Co",
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
@patch("app.api.v1.sources.create_pool")
async def test_upload_txt_file(mock_pool, client: AsyncClient):
    """Uploading a .txt file creates a source with type=upload."""
    mock_redis = AsyncMock()
    mock_pool.return_value = mock_redis

    ctx = await _setup(client, "upl-txt")

    resp = await client.post(
        "/v1/sources/upload",
        files={"file": ("notes.txt", b"Hello from upload", "text/plain")},
        data={"bot_profile_id": ctx["profile_id"]},
        headers=ctx["headers"],
    )
    assert resp.status_code == 201
    src = resp.json()
    assert src["source_type"] == "upload"
    assert src["name"] == "notes.txt"
    assert src["status"] == "pending"
    config = src["config"]
    assert config["original_filename"] == "notes.txt"
    assert config["file_size"] == len(b"Hello from upload")


@pytest.mark.asyncio
@patch("app.api.v1.sources.create_pool")
async def test_upload_triggers_ingest(mock_pool, client: AsyncClient):
    """Upload auto-enqueues an ingest job."""
    mock_redis = AsyncMock()
    mock_pool.return_value = mock_redis

    ctx = await _setup(client, "upl-ingest")

    resp = await client.post(
        "/v1/sources/upload",
        files={"file": ("data.csv", b"col1,col2\na,b", "text/csv")},
        data={"bot_profile_id": ctx["profile_id"]},
        headers=ctx["headers"],
    )
    assert resp.status_code == 201

    mock_redis.enqueue_job.assert_awaited_once()
    call_args = mock_redis.enqueue_job.call_args
    assert call_args[0][0] == "ingest_source"


@pytest.mark.asyncio
async def test_upload_unsupported_type(client: AsyncClient):
    """Uploading an unsupported file type returns 422."""
    ctx = await _setup(client, "upl-bad-ext")

    resp = await client.post(
        "/v1/sources/upload",
        files={"file": ("script.exe", b"\x00\x01", "application/octet-stream")},
        data={"bot_profile_id": ctx["profile_id"]},
        headers=ctx["headers"],
    )
    assert resp.status_code == 422
    assert "Unsupported file type" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_file_too_large(client: AsyncClient):
    """Uploading a file exceeding MAX_FILE_SIZE returns 422."""
    ctx = await _setup(client, "upl-large")

    # Patch MAX_FILE_SIZE to a small value for testing
    with patch("app.api.v1.sources.MAX_FILE_SIZE", 100):
        resp = await client.post(
            "/v1/sources/upload",
            files={"file": ("big.txt", b"x" * 200, "text/plain")},
            data={"bot_profile_id": ctx["profile_id"]},
            headers=ctx["headers"],
        )
    assert resp.status_code == 422
    assert "too large" in resp.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.v1.sources.create_pool")
async def test_upload_cross_tenant_isolation(mock_pool, client: AsyncClient):
    """Tenant B cannot see sources uploaded by tenant A."""
    mock_redis = AsyncMock()
    mock_pool.return_value = mock_redis

    ctx_a = await _setup(client, "upl-iso-a")
    ctx_b = await _setup(client, "upl-iso-b")

    resp = await client.post(
        "/v1/sources/upload",
        files={"file": ("secret.txt", b"Top secret", "text/plain")},
        data={"bot_profile_id": ctx_a["profile_id"]},
        headers=ctx_a["headers"],
    )
    assert resp.status_code == 201
    src_id = resp.json()["id"]

    # Tenant B cannot access
    resp = await client.get(f"/v1/sources/{src_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404
