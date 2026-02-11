"""A/B tests — verify each bot only retrieves its own sources during RAG.

Creates two bots (Bot A, Bot B) under the same tenant with different sources,
then verifies vector search is called with the correct bot_profile_id filter.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


async def _setup_ab(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + two bot profiles, return headers + IDs."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "AB Test Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    tenant_id = data["tenant"]["id"]

    # Create Bot A — product support
    resp_a = await client.post("/v1/bot-profiles", json={
        "name": "Product Bot",
        "system_prompt": "You answer product questions.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    assert resp_a.status_code == 201
    bot_a_id = resp_a.json()["id"]

    # Create Bot B — HR support
    resp_b = await client.post("/v1/bot-profiles", json={
        "name": "HR Bot",
        "system_prompt": "You answer HR questions.",
        "model": "gpt-4o-mini",
    }, headers=headers)
    assert resp_b.status_code == 201
    bot_b_id = resp_b.json()["id"]

    # Create Source for Bot A
    resp_src_a = await client.post("/v1/sources", json={
        "bot_profile_id": bot_a_id,
        "name": "Product Docs",
        "source_type": "text",
        "content": "Our product supports file uploads and URL ingestion.",
    }, headers=headers)
    assert resp_src_a.status_code == 201
    source_a_id = resp_src_a.json()["id"]

    # Create Source for Bot B
    resp_src_b = await client.post("/v1/sources", json={
        "bot_profile_id": bot_b_id,
        "name": "HR Handbook",
        "source_type": "text",
        "content": "Employees get 25 vacation days per year.",
    }, headers=headers)
    assert resp_src_b.status_code == 201
    source_b_id = resp_src_b.json()["id"]

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "bot_a_id": bot_a_id,
        "bot_b_id": bot_b_id,
        "source_a_id": source_a_id,
        "source_b_id": source_b_id,
    }


def _mock_llm_response(content: str = "Mock response"):
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 30

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_search_results(bot_profile_id: str, source_id: str, content: str):
    """Return fake Qdrant search results scoped to a specific bot."""
    return [
        {
            "id": f"chunk-{bot_profile_id[:8]}-001",
            "score": 0.91,
            "payload": {
                "content": content,
                "source_id": source_id,
                "tenant_id": "t1",
                "bot_profile_id": bot_profile_id,
            },
        },
    ]


@pytest.mark.asyncio
async def test_bot_a_only_searches_own_sources(client: AsyncClient):
    """Bot A's chat request calls search_chunks with Bot A's profile_id."""
    ctx = await _setup_ab(client, "ab-iso-a")

    product_chunks = _make_search_results(
        ctx["bot_a_id"], ctx["source_a_id"],
        "Our product supports file uploads and URL ingestion.",
    )

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=product_chunks)
    mock_llm = AsyncMock(return_value=_mock_llm_response("We support file uploads."))

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["bot_a_id"],
            "message": "What file types do you support?",
        }, headers=ctx["headers"])

    assert resp.status_code == 200
    data = resp.json()

    # Verify search was called with Bot A's profile_id
    mock_search.assert_called_once()
    search_kwargs = mock_search.call_args
    assert search_kwargs.kwargs["bot_profile_id"] == ctx["bot_a_id"]
    assert search_kwargs.kwargs["tenant_id"] == ctx["tenant_id"]

    # Verify the response used Bot A's chunks
    assert len(data["sources"]) == 1
    assert data["sources"][0]["source_id"] == ctx["source_a_id"]


@pytest.mark.asyncio
async def test_bot_b_only_searches_own_sources(client: AsyncClient):
    """Bot B's chat request calls search_chunks with Bot B's profile_id."""
    ctx = await _setup_ab(client, "ab-iso-b")

    hr_chunks = _make_search_results(
        ctx["bot_b_id"], ctx["source_b_id"],
        "Employees get 25 vacation days per year.",
    )

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_search = AsyncMock(return_value=hr_chunks)
    mock_llm = AsyncMock(return_value=_mock_llm_response("25 vacation days."))

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        resp = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["bot_b_id"],
            "message": "How many vacation days do I get?",
        }, headers=ctx["headers"])

    assert resp.status_code == 200
    data = resp.json()

    # Verify search was called with Bot B's profile_id
    mock_search.assert_called_once()
    search_kwargs = mock_search.call_args
    assert search_kwargs.kwargs["bot_profile_id"] == ctx["bot_b_id"]
    assert search_kwargs.kwargs["tenant_id"] == ctx["tenant_id"]

    # Verify the response used Bot B's chunks
    assert len(data["sources"]) == 1
    assert data["sources"][0]["source_id"] == ctx["source_b_id"]


@pytest.mark.asyncio
async def test_ab_sequential_no_cross_contamination(client: AsyncClient):
    """Chat with Bot A then Bot B — verify no source leakage between them."""
    ctx = await _setup_ab(client, "ab-seq")

    product_chunks = _make_search_results(
        ctx["bot_a_id"], ctx["source_a_id"],
        "Our product supports file uploads.",
    )
    hr_chunks = _make_search_results(
        ctx["bot_b_id"], ctx["source_b_id"],
        "25 vacation days per year.",
    )

    mock_embed = AsyncMock(return_value=[[0.1] * 1536])
    mock_llm = AsyncMock(return_value=_mock_llm_response("Response"))

    # Track search_chunks calls with side_effect based on bot_profile_id
    async def selective_search(*, query_vector, tenant_id, bot_profile_id, limit=5):
        if bot_profile_id == ctx["bot_a_id"]:
            return product_chunks
        elif bot_profile_id == ctx["bot_b_id"]:
            return hr_chunks
        return []

    mock_search = AsyncMock(side_effect=selective_search)

    with (
        patch("app.services.orchestrator.embed_texts", mock_embed),
        patch("app.services.orchestrator.search_chunks", mock_search),
        patch("app.services.orchestrator.acompletion", mock_llm),
    ):
        # Chat with Bot A
        resp_a = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["bot_a_id"],
            "message": "Tell me about the product",
        }, headers=ctx["headers"])

        # Chat with Bot B
        resp_b = await client.post("/v1/chat", json={
            "bot_profile_id": ctx["bot_b_id"],
            "message": "What about vacation?",
        }, headers=ctx["headers"])

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    # Bot A returned product sources only
    sources_a = resp_a.json()["sources"]
    assert len(sources_a) == 1
    assert sources_a[0]["source_id"] == ctx["source_a_id"]

    # Bot B returned HR sources only
    sources_b = resp_b.json()["sources"]
    assert len(sources_b) == 1
    assert sources_b[0]["source_id"] == ctx["source_b_id"]

    # Verify search was called exactly twice, once per bot
    assert mock_search.call_count == 2
    call_bots = [c.kwargs["bot_profile_id"] for c in mock_search.call_args_list]
    assert ctx["bot_a_id"] in call_bots
    assert ctx["bot_b_id"] in call_bots


@pytest.mark.asyncio
async def test_sources_api_filters_by_bot(client: AsyncClient):
    """GET /v1/sources?bot_profile_id= returns only that bot's sources."""
    ctx = await _setup_ab(client, "ab-filter")

    # List all sources
    resp_all = await client.get("/v1/sources", headers=ctx["headers"])
    assert resp_all.status_code == 200
    all_sources = resp_all.json()
    assert len(all_sources) == 2

    # Filter by Bot A
    resp_a = await client.get(
        f"/v1/sources?bot_profile_id={ctx['bot_a_id']}",
        headers=ctx["headers"],
    )
    assert resp_a.status_code == 200
    sources_a = resp_a.json()
    assert len(sources_a) == 1
    assert sources_a[0]["id"] == ctx["source_a_id"]
    assert sources_a[0]["bot_profile_id"] == ctx["bot_a_id"]
    assert sources_a[0]["name"] == "Product Docs"

    # Filter by Bot B
    resp_b = await client.get(
        f"/v1/sources?bot_profile_id={ctx['bot_b_id']}",
        headers=ctx["headers"],
    )
    assert resp_b.status_code == 200
    sources_b = resp_b.json()
    assert len(sources_b) == 1
    assert sources_b[0]["id"] == ctx["source_b_id"]
    assert sources_b[0]["bot_profile_id"] == ctx["bot_b_id"]
    assert sources_b[0]["name"] == "HR Handbook"


@pytest.mark.asyncio
async def test_source_cannot_be_assigned_to_wrong_bot(client: AsyncClient):
    """Creating a source with a bot_profile from a different tenant fails."""
    ctx = await _setup_ab(client, "ab-cross")

    # Create a separate tenant
    resp2 = await client.post("/v1/tenants", json={
        "tenant_name": "Other Co",
        "tenant_slug": "ab-cross-other",
        "owner_email": "other@test.com",
        "owner_password": "password1234",
    })
    other_headers = {"Authorization": f"Bearer {resp2.json()['api_token']}"}

    # Try to create source referencing first tenant's bot profile
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["bot_a_id"],
        "name": "Malicious Source",
        "source_type": "text",
        "content": "Trying to inject into another tenant's bot.",
    }, headers=other_headers)
    assert resp.status_code == 422
    assert "bot profile" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingestion_stores_correct_bot_profile_id(client: AsyncClient, session):
    """Verify the ingest worker stores bot_profile_id in Qdrant payloads."""
    ctx = await _setup_ab(client, "ab-ingest")

    mock_embed = AsyncMock(return_value=[[0.5] * 1536])
    mock_ensure = AsyncMock()
    mock_delete = AsyncMock()
    mock_upsert = AsyncMock()

    # Create a session factory that returns our test session
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session_factory():
        yield session

    with (
        patch("app.workers.ingest.async_session_factory", mock_session_factory),
        patch("app.workers.ingest.embed_texts", mock_embed),
        patch("app.workers.ingest.ensure_collection", mock_ensure),
        patch("app.workers.ingest.delete_by_source", mock_delete),
        patch("app.workers.ingest.upsert_chunks", mock_upsert),
    ):
        from app.workers.ingest import ingest_source

        result = await ingest_source(
            {},
            source_id=ctx["source_a_id"],
            tenant_id=ctx["tenant_id"],
        )

    assert "error" not in result, f"Ingestion failed: {result}"
    assert result["chunk_count"] > 0

    # Verify upsert was called with correct bot_profile_id in payload
    mock_upsert.assert_called_once()
    points = mock_upsert.call_args[0][0]
    for point in points:
        assert point["payload"]["bot_profile_id"] == ctx["bot_a_id"]
        assert point["payload"]["tenant_id"] == ctx["tenant_id"]
        assert point["payload"]["source_id"] == ctx["source_a_id"]


@pytest.mark.asyncio
async def test_vector_search_filter_structure(client: AsyncClient):
    """Verify the Qdrant filter includes both tenant_id and bot_profile_id."""
    from app.services.vector_store import search_chunks

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points = AsyncMock(return_value=mock_response)

    with patch("app.services.vector_store.get_qdrant_client", AsyncMock(return_value=mock_client)):
        await search_chunks(
            query_vector=[0.1] * 1536,
            tenant_id="tenant-123",
            bot_profile_id="bot-456",
            limit=5,
        )

    # Verify the filter passed to Qdrant
    mock_client.query_points.assert_called_once()
    call_kwargs = mock_client.query_points.call_args.kwargs
    query_filter = call_kwargs["query_filter"]

    # Must have exactly 2 "must" conditions: tenant_id + bot_profile_id
    assert len(query_filter.must) == 2
    filter_keys = {cond.key for cond in query_filter.must}
    assert filter_keys == {"tenant_id", "bot_profile_id"}

    # Verify values
    for cond in query_filter.must:
        if cond.key == "tenant_id":
            assert cond.match.value == "tenant-123"
        elif cond.key == "bot_profile_id":
            assert cond.match.value == "bot-456"
