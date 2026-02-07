"""Source parent/child hierarchy tests."""

import uuid

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + create a bot profile, return headers + profile_id."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Hierarchy Co",
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
async def test_batch_create(client: AsyncClient):
    """POST /v1/sources/batch creates parent + children."""
    ctx = await _setup(client, "hier-batch")

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Docs Collection",
        "source_type": "url",
        "children": [
            {"name": "Page 1", "source_type": "url", "config": {"url": "https://example.com/1"}},
            {"name": "Page 2", "source_type": "url", "config": {"url": "https://example.com/2"}},
            {"name": "Page 3", "source_type": "url", "config": {"url": "https://example.com/3"}},
        ],
    }, headers=ctx["headers"])
    assert resp.status_code == 201
    data = resp.json()
    parent = data["parent"]
    children = data["children"]

    assert parent["name"] == "Docs Collection"
    assert parent["children_count"] == 3
    assert parent["parent_id"] is None
    assert len(children) == 3
    for c in children:
        assert c["parent_id"] == parent["id"]


@pytest.mark.asyncio
async def test_batch_create_empty_children_rejected(client: AsyncClient):
    """POST /v1/sources/batch with no children returns 422."""
    ctx = await _setup(client, "hier-batch-empty")

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Empty",
        "source_type": "url",
        "children": [],
    }, headers=ctx["headers"])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_top_level_only(client: AsyncClient):
    """GET /v1/sources (default) returns only top-level sources."""
    ctx = await _setup(client, "hier-list-top")
    headers = ctx["headers"]

    # Create a batch (parent + children)
    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Parent",
        "source_type": "url",
        "children": [
            {"name": "Child 1", "source_type": "url", "config": {"url": "https://example.com"}},
        ],
    }, headers=headers)
    assert resp.status_code == 201

    # Create a standalone source
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Standalone",
        "source_type": "text",
    }, headers=headers)
    assert resp.status_code == 201

    # Default list: only top-level
    resp = await client.get("/v1/sources", headers=headers)
    sources = resp.json()
    assert len(sources) == 2
    names = {s["name"] for s in sources}
    assert names == {"Parent", "Standalone"}

    # include_children=true: flat list of all
    resp = await client.get("/v1/sources?include_children=true", headers=headers)
    sources = resp.json()
    assert len(sources) == 3


@pytest.mark.asyncio
async def test_list_children_of_parent(client: AsyncClient):
    """GET /v1/sources/{id}/children returns children of a parent."""
    ctx = await _setup(client, "hier-children")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Parent",
        "source_type": "url",
        "children": [
            {"name": "Child A", "source_type": "url", "config": {"url": "https://a.com"}},
            {"name": "Child B", "source_type": "url", "config": {"url": "https://b.com"}},
        ],
    }, headers=headers)
    parent_id = resp.json()["parent"]["id"]

    resp = await client.get(f"/v1/sources/{parent_id}/children", headers=headers)
    assert resp.status_code == 200
    children = resp.json()
    assert len(children) == 2
    assert children[0]["name"] == "Child A"
    assert children[1]["name"] == "Child B"


@pytest.mark.asyncio
async def test_aggregated_parent_status(client: AsyncClient):
    """Parent status reflects aggregated child statuses."""
    ctx = await _setup(client, "hier-agg-status")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Agg Parent",
        "source_type": "url",
        "children": [
            {"name": "C1", "source_type": "text", "content": "hello"},
            {"name": "C2", "source_type": "text", "content": "world"},
        ],
    }, headers=headers)
    parent_id = resp.json()["parent"]["id"]

    # Initially all children are pending → parent is pending
    resp = await client.get(f"/v1/sources/{parent_id}", headers=headers)
    assert resp.json()["status"] == "pending"
    assert resp.json()["children_count"] == 2


@pytest.mark.asyncio
async def test_aggregated_parent_chunk_count(client: AsyncClient):
    """Parent chunk_count sums children chunk_counts."""
    ctx = await _setup(client, "hier-agg-chunks")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Chunk Parent",
        "source_type": "url",
        "children": [
            {"name": "C1", "source_type": "text"},
            {"name": "C2", "source_type": "text"},
        ],
    }, headers=headers)
    parent_id = resp.json()["parent"]["id"]

    # All children have chunk_count=0, so parent should be 0
    resp = await client.get(f"/v1/sources/{parent_id}", headers=headers)
    assert resp.json()["chunk_count"] == 0


@pytest.mark.asyncio
async def test_delete_parent_cascades(client: AsyncClient):
    """Deleting a parent soft-deletes all children."""
    ctx = await _setup(client, "hier-del-cascade")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "To Delete",
        "source_type": "url",
        "children": [
            {"name": "Child 1", "source_type": "url", "config": {"url": "https://a.com"}},
            {"name": "Child 2", "source_type": "url", "config": {"url": "https://b.com"}},
        ],
    }, headers=headers)
    data = resp.json()
    parent_id = data["parent"]["id"]
    child_ids = [c["id"] for c in data["children"]]

    # Delete parent
    resp = await client.delete(f"/v1/sources/{parent_id}", headers=headers)
    assert resp.status_code == 204

    # Parent is soft-deleted
    resp = await client.get(f"/v1/sources/{parent_id}", headers=headers)
    assert resp.json()["is_active"] is False

    # Children are soft-deleted
    for cid in child_ids:
        resp = await client.get(f"/v1/sources/{cid}", headers=headers)
        assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_single_child(client: AsyncClient):
    """Deleting one child does not affect parent or siblings."""
    ctx = await _setup(client, "hier-del-child")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Parent",
        "source_type": "url",
        "children": [
            {"name": "Keep", "source_type": "url", "config": {"url": "https://a.com"}},
            {"name": "Remove", "source_type": "url", "config": {"url": "https://b.com"}},
        ],
    }, headers=headers)
    data = resp.json()
    parent_id = data["parent"]["id"]
    keep_id = data["children"][0]["id"]
    remove_id = data["children"][1]["id"]

    resp = await client.delete(f"/v1/sources/{remove_id}", headers=headers)
    assert resp.status_code == 204

    # Parent still active, children_count drops to 1
    resp = await client.get(f"/v1/sources/{parent_id}", headers=headers)
    parent = resp.json()
    assert parent["is_active"] is True
    assert parent["children_count"] == 1

    # Sibling still active
    resp = await client.get(f"/v1/sources/{keep_id}", headers=headers)
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_ingest_children_endpoint(client: AsyncClient):
    """POST /v1/sources/{id}/ingest-children enqueues jobs for children."""
    ctx = await _setup(client, "hier-ingest-ch")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Ingest Parent",
        "source_type": "url",
        "children": [
            {"name": "C1", "source_type": "text", "content": "hello"},
            {"name": "C2", "source_type": "text", "content": "world"},
        ],
    }, headers=headers)
    parent_id = resp.json()["parent"]["id"]

    # Mock Redis to avoid real connection
    from unittest.mock import AsyncMock, patch

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.sources.create_pool", return_value=mock_redis):
        resp = await client.post(f"/v1/sources/{parent_id}/ingest-children", headers=headers)

    assert resp.status_code == 202
    data = resp.json()
    assert data["enqueued"] == 2
    assert mock_redis.enqueue_job.call_count == 2


@pytest.mark.asyncio
async def test_standalone_source_backward_compat(client: AsyncClient):
    """Standalone sources (no parent) still work as before."""
    ctx = await _setup(client, "hier-compat")
    headers = ctx["headers"]

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Solo",
        "source_type": "text",
        "content": "standalone content",
    }, headers=headers)
    assert resp.status_code == 201
    src = resp.json()
    assert src["parent_id"] is None
    assert src["children_count"] == 0

    # Shows up in list
    resp = await client.get("/v1/sources", headers=headers)
    assert any(s["name"] == "Solo" for s in resp.json())


@pytest.mark.asyncio
async def test_cross_tenant_isolation_hierarchy(client: AsyncClient):
    """Tenant B cannot access tenant A's parent or children."""
    ctx_a = await _setup(client, "hier-iso-a")
    ctx_b = await _setup(client, "hier-iso-b")

    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx_a["profile_id"],
        "name": "Tenant A Parent",
        "source_type": "url",
        "children": [
            {"name": "A Child", "source_type": "url", "config": {"url": "https://a.com"}},
        ],
    }, headers=ctx_a["headers"])
    parent_id = resp.json()["parent"]["id"]
    child_id = resp.json()["children"][0]["id"]

    # Tenant B cannot see parent
    resp = await client.get(f"/v1/sources/{parent_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404

    # Tenant B cannot see children
    resp = await client.get(f"/v1/sources/{parent_id}/children", headers=ctx_b["headers"])
    assert resp.status_code == 404

    # Tenant B cannot see child directly
    resp = await client.get(f"/v1/sources/{child_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_parent_validation(client: AsyncClient):
    """Parent must exist, same tenant, same bot_profile, no nesting."""
    ctx = await _setup(client, "hier-validate")
    headers = ctx["headers"]

    # Create a parent+child batch
    resp = await client.post("/v1/sources/batch", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Parent",
        "source_type": "url",
        "children": [
            {"name": "Child", "source_type": "url", "config": {"url": "https://a.com"}},
        ],
    }, headers=headers)
    parent_id = resp.json()["parent"]["id"]
    child_id = resp.json()["children"][0]["id"]

    # Cannot create source with non-existent parent
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Bad Parent",
        "source_type": "text",
        "parent_id": str(uuid.uuid4()),
    }, headers=headers)
    assert resp.status_code == 404

    # Cannot create grandchild (child's child)
    resp = await client.post("/v1/sources", json={
        "bot_profile_id": ctx["profile_id"],
        "name": "Grandchild",
        "source_type": "text",
        "parent_id": child_id,
    }, headers=headers)
    assert resp.status_code == 422

    # Cannot use different bot profile for child
    resp2 = await client.post("/v1/bot-profiles", json={"name": "Other Bot"}, headers=headers)
    other_bp_id = resp2.json()["id"]

    resp = await client.post("/v1/sources", json={
        "bot_profile_id": other_bp_id,
        "name": "Wrong Bot",
        "source_type": "text",
        "parent_id": parent_id,
    }, headers=headers)
    assert resp.status_code == 422
