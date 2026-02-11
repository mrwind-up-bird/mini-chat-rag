"""Integration tests for the ingestion pipeline (mocked embedding + Qdrant)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.ingest import ingest_source


async def _setup_source(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + bot profile + text source."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Ingest Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    resp = await client.post("/v1/bot-profiles", json={"name": "Bot"}, headers=headers)
    profile_id = resp.json()["id"]

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": profile_id,
        "name": "Knowledge Base",
        "source_type": "text",
        "content": "MiniRAG is a modular RAG platform. It supports multi-tenancy and provides "
                   "an API-first approach to building chatbots. Each tenant has isolated data. "
                   "The system uses Qdrant for vector storage and PostgreSQL for metadata. "
                   "LiteLLM provides provider-agnostic LLM access.",
    }, headers=headers)
    source = resp.json()

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "source_id": source["id"],
    }


def _mock_embedding(n_texts: int) -> list[list[float]]:
    """Return fake 1536-dim vectors."""
    return [[0.1] * 1536 for _ in range(n_texts)]


@pytest.mark.asyncio
async def test_ingest_source_end_to_end(client: AsyncClient, test_session_factory):
    """Full ingestion: chunk → embed (mocked) → upsert (mocked) → verify DB state."""
    ctx = await _setup_source(client, "ingest-e2e")

    mock_embed = AsyncMock(side_effect=lambda texts, **kw: _mock_embedding(len(texts)))
    mock_ensure = AsyncMock()
    mock_upsert = AsyncMock()
    mock_delete = AsyncMock()

    with (
        patch("app.workers.ingest.async_session_factory", test_session_factory),
        patch("app.workers.ingest.embed_texts", mock_embed),
        patch("app.workers.ingest.ensure_collection", mock_ensure),
        patch("app.workers.ingest.upsert_chunks", mock_upsert),
        patch("app.workers.ingest.delete_by_source", mock_delete),
    ):
        result = await ingest_source(
            {},
            source_id=ctx["source_id"],
            tenant_id=ctx["tenant_id"],
        )

    # Task should succeed
    assert "error" not in result, f"Ingest failed: {result}"
    assert result["document_count"] == 1
    assert result["chunk_count"] > 0

    # Verify embedding was called
    mock_embed.assert_called_once()
    texts_arg = mock_embed.call_args[0][0]
    assert len(texts_arg) == result["chunk_count"]

    # Verify Qdrant upsert was called with correct payloads
    mock_upsert.assert_called_once()
    points = mock_upsert.call_args[1].get("points") or mock_upsert.call_args[0][0]
    assert len(points) == result["chunk_count"]
    for p in points:
        assert p["payload"]["tenant_id"] == ctx["tenant_id"]
        assert p["payload"]["source_id"] == ctx["source_id"]

    # Verify source status updated in DB
    resp = await client.get(
        f"/v1/sources/{ctx['source_id']}", headers=ctx["headers"]
    )
    assert resp.status_code == 200
    source_data = resp.json()
    assert source_data["status"] == "ready"
    assert source_data["chunk_count"] == result["chunk_count"]
    assert source_data["document_count"] == 1
    assert source_data["last_refreshed_at"] is not None


@pytest.mark.asyncio
async def test_ingest_empty_content(client: AsyncClient, test_session_factory):
    """Ingesting a source with no content should result in error status."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Empty Co",
        "tenant_slug": "ingest-empty",
        "owner_email": "empty@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}

    resp = await client.post("/v1/bot-profiles", json={"name": "Bot"}, headers=headers)
    profile_id = resp.json()["id"]

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": profile_id,
        "name": "Empty Source",
        "source_type": "text",
        "content": "",
    }, headers=headers)
    source = resp.json()

    with patch("app.workers.ingest.async_session_factory", test_session_factory):
        result = await ingest_source(
            {},
            source_id=source["id"],
            tenant_id=data["tenant"]["id"],
        )

    assert result.get("error") == "no_content"

    # Source should be marked as error
    resp = await client.get(f"/v1/sources/{source['id']}", headers=headers)
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_ingest_trigger_endpoint(client: AsyncClient):
    """POST /v1/sources/{id}/ingest should attempt to enqueue (mocked Redis)."""
    ctx = await _setup_source(client, "ingest-trigger")

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock()
    mock_pool.aclose = AsyncMock()

    with patch("app.api.v1.sources.create_pool", return_value=mock_pool):
        resp = await client.post(
            f"/v1/sources/{ctx['source_id']}/ingest",
            headers=ctx["headers"],
        )

    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"
    mock_pool.enqueue_job.assert_called_once_with(
        "ingest_source",
        source_id=ctx["source_id"],
        tenant_id=ctx["tenant_id"],
    )
