"""Tests for NyxCore Axiom integration — source creation, ingestion, and chat retrieval."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# Ensure ENCRYPTION_KEY is set before importing security module
os.environ.setdefault(
    "ENCRYPTION_KEY", "49tvPksgUV23soH7nAxE8cSLDp7BZ8FGId_VLlZb7Hs="
)

from app.core.security import decrypt_value, encrypt_value  # noqa: E402, I001
from app.services.nyxcore import AxiomChunk, search_axiom  # noqa: E402
from app.services.orchestrator import RetrievedChunk, _merge_chunks  # noqa: E402
from app.workers.ingest import ingest_source  # noqa: E402


# ── Fixtures / helpers ───────────────────────────────────────────


async def _setup_nyxcore(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + bot profile + NyxCore source."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "NyxCore Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    resp = await client.post("/v1/bot-profiles", json={
        "name": "NyxCore Bot",
        "system_prompt": "You are a helpful assistant.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    profile_id = resp.json()["id"]

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
    }


def _axiom_search_results() -> list[dict]:
    """Fake NyxCore Axiom API search response results."""
    return [
        {
            "content": "Neue Mitarbeiter erhalten am ersten Tag einen Laptop.",
            "heading": "Onboarding Prozess",
            "filename": "hr-handbuch.md",
            "authority": "mandatory",
            "score": 0.95,
            "chunkId": "nyx-chunk-001",
            "documentId": "nyx-doc-001",
        },
        {
            "content": "Die Coding Guidelines beschreiben den Stil.",
            "heading": "Coding Standards",
            "filename": "coding-standards.md",
            "authority": "guideline",
            "score": 0.82,
            "chunkId": "nyx-chunk-002",
            "documentId": "nyx-doc-002",
        },
    ]


def _axiom_documents() -> list[dict]:
    """Fake NyxCore Axiom API documents list."""
    return [
        {
            "id": "nyx-doc-001",
            "filename": "hr-handbuch.md",
            "mimeType": "text/markdown",
            "fileSize": 24576,
            "status": "ready",
            "chunkCount": 12,
            "authority": "mandatory",
        },
        {
            "id": "nyx-doc-002",
            "filename": "coding-standards.md",
            "mimeType": "text/markdown",
            "fileSize": 8192,
            "status": "ready",
            "chunkCount": 5,
            "authority": "guideline",
        },
    ]


# ── Source CRUD tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_nyxcore_source(client: AsyncClient):
    """Creating a NyxCore source encrypts the API token in config."""
    ctx = await _setup_nyxcore(client, "nyx-create")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "NyxCore BRbase",
        "source_type": "nyxcore",
        "config": {
            "base_url": "https://nyxcore.cloud",
            "api_token": "nyx_ax_test_token_123",
            "limit": 10,
            "authority": ["mandatory", "guideline"],
        },
    }, headers=ctx["headers"])

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source_type"] == "nyxcore"
    assert data["status"] == "pending"

    # API response should NOT expose the encrypted token
    config = data["config"]
    assert "encrypted_token" not in config
    assert "api_token" not in config
    assert config["has_api_token"] is True
    assert config["base_url"] == "https://nyxcore.cloud"
    assert config["limit"] == 10
    assert config["authority"] == ["mandatory", "guideline"]


@pytest.mark.asyncio
async def test_create_nyxcore_source_no_token(client: AsyncClient):
    """Creating a NyxCore source without a token still succeeds."""
    ctx = await _setup_nyxcore(client, "nyx-no-token")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "NyxCore No Token",
        "source_type": "nyxcore",
        "config": {
            "base_url": "https://nyxcore.cloud",
        },
    }, headers=ctx["headers"])

    assert resp.status_code == 201
    config = resp.json()["config"]
    assert "has_api_token" not in config


# ── Ingestion tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_nyxcore_validates_connection(client: AsyncClient, test_session_factory):
    """Ingesting a NyxCore source validates the connection by listing documents."""
    ctx = await _setup_nyxcore(client, "nyx-ingest")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "NyxCore Ingest Test",
        "source_type": "nyxcore",
        "config": {
            "base_url": "https://nyxcore.cloud",
            "api_token": "nyx_ax_test_token_ingest",
        },
    }, headers=ctx["headers"])
    source_id = resp.json()["id"]

    mock_list = AsyncMock(return_value=_axiom_documents())

    with (
        patch("app.workers.ingest.async_session_factory", test_session_factory),
        patch("app.workers.ingest.list_axiom_documents", mock_list),
    ):
        result = await ingest_source({}, source_id=source_id, tenant_id=ctx["tenant_id"])

    assert "error" not in result, f"Ingest failed: {result}"
    assert result["document_count"] == 2
    assert result["chunk_count"] == 17  # 12 + 5

    # Verify source is now READY
    resp = await client.get(f"/v1/sources/{source_id}", headers=ctx["headers"])
    assert resp.json()["status"] == "ready"
    assert resp.json()["document_count"] == 2
    assert resp.json()["chunk_count"] == 17


@pytest.mark.asyncio
async def test_ingest_nyxcore_no_token_fails(client: AsyncClient, test_session_factory):
    """Ingesting a NyxCore source without a token should fail."""
    ctx = await _setup_nyxcore(client, "nyx-ingest-notoken")

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "NyxCore No Token",
        "source_type": "nyxcore",
        "config": {"base_url": "https://nyxcore.cloud"},
    }, headers=ctx["headers"])
    source_id = resp.json()["id"]

    with patch("app.workers.ingest.async_session_factory", test_session_factory):
        result = await ingest_source({}, source_id=source_id, tenant_id=ctx["tenant_id"])

    assert result.get("error") == "no_token"

    resp = await client.get(f"/v1/sources/{source_id}", headers=ctx["headers"])
    assert resp.json()["status"] == "error"


# ── Chat with NyxCore context ────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_with_nyxcore_source(client: AsyncClient, test_session_factory):
    """Chat should query NyxCore sources and include results in LLM context."""
    ctx = await _setup_nyxcore(client, "nyx-chat")

    # Create and "ingest" a NyxCore source
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "NyxCore Chat Source",
        "source_type": "nyxcore",
        "config": {
            "base_url": "https://nyxcore.cloud",
            "api_token": "nyx_ax_test_chat_token",
            "limit": 5,
        },
    }, headers=ctx["headers"])
    source_id = resp.json()["id"]

    # Mark source as ready (simulate successful ingest)
    mock_list = AsyncMock(return_value=_axiom_documents())
    with (
        patch("app.workers.ingest.async_session_factory", test_session_factory),
        patch("app.workers.ingest.list_axiom_documents", mock_list),
    ):
        await ingest_source({}, source_id=source_id, tenant_id=ctx["tenant_id"])

    # Now chat — mock Axiom search + LLM
    axiom_chunks = [
        AxiomChunk(
            content="Neue Mitarbeiter erhalten am ersten Tag einen Laptop.",
            heading="Onboarding",
            filename="hr-handbuch.md",
            authority="mandatory",
            score=0.95,
            chunk_id="nyx-c1",
            document_id="nyx-d1",
        ),
    ]
    mock_axiom_search = AsyncMock(return_value=axiom_chunks)
    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_qdrant_search = AsyncMock(return_value=[])

    usage = MagicMock()
    usage.prompt_tokens = 200
    usage.completion_tokens = 50
    message = MagicMock()
    message.content = "Am ersten Tag erhalten Sie einen Laptop."
    choice = MagicMock()
    choice.message = message
    mock_response = MagicMock()
    mock_response.choices = [choice]
    mock_response.usage = usage
    mock_llm = AsyncMock(return_value=mock_response)

    with (
        patch("app.api.v1.chat.search_axiom", mock_axiom_search),
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_qdrant_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["profile_id"],
            "message": "Wie funktioniert das Onboarding?",
        }, headers=ctx["headers"])

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["message"]["content"] == "Am ersten Tag erhalten Sie einen Laptop."

    # Verify NyxCore search was called
    mock_axiom_search.assert_called_once()
    call_kwargs = mock_axiom_search.call_args.kwargs
    assert "Onboarding" in call_kwargs["query"]

    # Verify the LLM received NyxCore context (check system prompt)
    llm_call = mock_llm.call_args
    messages = llm_call.kwargs.get("messages") or llm_call[1].get("messages")
    system_msg = messages[0]["content"]
    assert "MANDATORY" in system_msg
    assert "Laptop" in system_msg

    # Verify sources include the NyxCore chunk
    assert len(data["sources"]) == 1
    assert data["sources"][0]["chunk_id"] == "nyx-c1"


# ── NyxCore service unit tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_search_axiom_parses_response():
    """search_axiom correctly parses the Axiom API response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "ok": True,
        "results": _axiom_search_results(),
        "requestId": "req-test",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.nyxcore.httpx.AsyncClient", return_value=mock_client):
        results = await search_axiom(
            api_token="nyx_ax_test",
            query="Onboarding",
            limit=5,
        )

    assert len(results) == 2
    assert results[0].content == "Neue Mitarbeiter erhalten am ersten Tag einen Laptop."
    assert results[0].authority == "mandatory"
    assert results[0].chunk_id == "nyx-chunk-001"
    assert results[1].authority == "guideline"


# ── Merge chunks unit test ───────────────────────────────────────


def test_merge_chunks_orders_by_authority():
    """External chunks with mandatory authority should come first."""
    local = [
        RetrievedChunk(chunk_id="local-1", content="Local chunk", score=0.9, source_id="s1"),
    ]
    external = [
        RetrievedChunk(
            chunk_id="nyx-1", content="Mandatory rule", score=0.8,
            source_id="s2", authority="mandatory", source_label="rules.md",
        ),
        RetrievedChunk(
            chunk_id="nyx-2", content="Guideline", score=0.7,
            source_id="s2", authority="guideline", source_label="guide.md",
        ),
    ]

    merged = _merge_chunks(external, local)

    assert len(merged) == 3
    # Mandatory first, then guideline, then local (no authority = last)
    assert merged[0].chunk_id == "nyx-1"
    assert merged[1].chunk_id == "nyx-2"
    assert merged[2].chunk_id == "local-1"


# ── Token encryption test ───────────────────────────────────────


def test_token_encryption_roundtrip():
    """Token should survive encrypt → decrypt roundtrip."""
    token = "nyx_ax_9ZXu8hnuZJVWhNhTxx6nxSowR-s8KLjSTXqFnjIKTWw"
    encrypted = encrypt_value(token)
    assert encrypted != token
    assert decrypt_value(encrypted) == token
