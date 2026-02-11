"""Tests for URL content extraction and ingestion."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.services.html_extract import html_to_text
from app.workers.ingest import ingest_source

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <script>var x = 1;</script>
  <style>.hidden { display: none; }</style>
  <h1>Welcome to MiniRAG</h1>
  <p>This is a <strong>test</strong> page with some content.</p>
  <div>Another paragraph here.</div>
</body>
</html>
"""


def test_html_to_text_basic():
    """html_to_text extracts visible text and strips script/style."""
    result = html_to_text(SAMPLE_HTML)
    assert "Welcome to MiniRAG" in result
    assert "test" in result
    assert "Another paragraph here." in result
    assert "var x = 1" not in result
    assert ".hidden" not in result
    assert "<h1>" not in result


def test_html_to_text_empty():
    """html_to_text handles empty input."""
    assert html_to_text("") == ""


def test_html_to_text_plain_text():
    """html_to_text passes through plain text."""
    assert html_to_text("just plain text") == "just plain text"


async def _setup_url_source(
    client: AsyncClient,
    slug: str,
    url: str = "https://example.com/page",
) -> dict:
    """Bootstrap tenant + bot profile + URL source."""
    resp = await client.post(
        "/v1/tenants",
        json={
            "tenant_name": "URL Co",
            "tenant_slug": slug,
            "owner_email": f"{slug}@test.com",
            "owner_password": "password1234",
        },
    )
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    resp = await client.post("/v1/bot-profiles", json={"name": "Bot"}, headers=headers)
    profile_id = resp.json()["id"]

    resp = await client.post(
        "/v1/sources",
        json={
            "bot_profile_id": profile_id,
            "name": "URL Source",
            "source_type": "url",
            "config": {"url": url},
        },
        headers=headers,
    )
    source = resp.json()

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "source_id": source["id"],
    }


def _mock_embedding(n_texts: int) -> list[list[float]]:
    return [[0.1] * 1536 for _ in range(n_texts)]


@pytest.mark.asyncio
async def test_url_source_extraction(client: AsyncClient, test_session_factory):
    """URL source: mock httpx response with HTML, verify text extraction in pipeline."""
    ctx = await _setup_url_source(client, "url-extract")

    mock_response = httpx.Response(
        200, text=SAMPLE_HTML, request=httpx.Request("GET", "https://example.com/page")
    )

    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

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
        patch("app.workers.ingest.httpx.AsyncClient", return_value=mock_http_client),
    ):
        result = await ingest_source(
            {},
            source_id=ctx["source_id"],
            tenant_id=ctx["tenant_id"],
        )

    assert "error" not in result, f"Ingest failed: {result}"
    assert result["document_count"] == 1
    assert result["chunk_count"] > 0
    mock_http_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_url_source_fetch_error(client: AsyncClient, test_session_factory):
    """URL source: HTTP 404 should mark source as ERROR."""
    ctx = await _setup_url_source(client, "url-error")

    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://example.com/page"),
            response=httpx.Response(404),
        )
    )
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.workers.ingest.async_session_factory", test_session_factory),
        patch("app.workers.ingest.httpx.AsyncClient", return_value=mock_http_client),
    ):
        result = await ingest_source(
            {},
            source_id=ctx["source_id"],
            tenant_id=ctx["tenant_id"],
        )

    assert "error" in result

    # Source should be marked as error
    resp = await client.get(f"/v1/sources/{ctx['source_id']}", headers=ctx["headers"])
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_url_source_ingest_e2e(client: AsyncClient, test_session_factory):
    """Full URL pipeline: fetch → chunk → embed → upsert, verify DB state."""
    ctx = await _setup_url_source(client, "url-e2e")

    html_content = (
        "<html><body><p>" + "This is test content for chunking. " * 50 + "</p></body></html>"
    )
    mock_response = httpx.Response(
        200, text=html_content, request=httpx.Request("GET", "https://example.com/page")
    )

    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

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
        patch("app.workers.ingest.httpx.AsyncClient", return_value=mock_http_client),
    ):
        result = await ingest_source(
            {},
            source_id=ctx["source_id"],
            tenant_id=ctx["tenant_id"],
        )

    assert "error" not in result
    assert result["document_count"] == 1
    assert result["chunk_count"] > 0

    # Verify source status in DB
    resp = await client.get(f"/v1/sources/{ctx['source_id']}", headers=ctx["headers"])
    source_data = resp.json()
    assert source_data["status"] == "ready"
    assert source_data["chunk_count"] == result["chunk_count"]
    assert source_data["last_refreshed_at"] is not None
