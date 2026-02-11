"""Webhook CRUD and dispatch tests."""

import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, slug: str) -> dict:
    """Bootstrap tenant + create API token, return headers + tenant info."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Webhook Co",
        "tenant_slug": slug,
        "owner_email": f"{slug}@test.com",
        "owner_password": "password1234",
    })
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    return {"headers": headers, "tenant": data["tenant"]}


@pytest.mark.asyncio
async def test_create_webhook(client: AsyncClient):
    ctx = await _setup(client, "wh-create")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["source.ingested", "source.failed"],
        "description": "My hook",
        "secret": "my-custom-secret",
    }, headers=ctx["headers"])
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/hook"
    assert data["events"] == ["source.ingested", "source.failed"]
    assert data["description"] == "My hook"
    assert data["secret"] == "my-custom-secret"  # noqa: S105
    assert data["has_secret"] is True
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_webhook_auto_secret(client: AsyncClient):
    ctx = await _setup(client, "wh-auto-secret")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["chat.message"],
    }, headers=ctx["headers"])
    assert resp.status_code == 201
    data = resp.json()
    # Secret should be auto-generated and returned
    assert "secret" in data
    assert len(data["secret"]) > 20
    assert data["has_secret"] is True


@pytest.mark.asyncio
async def test_list_webhooks(client: AsyncClient):
    ctx = await _setup(client, "wh-list")
    headers = ctx["headers"]

    # Create two webhooks
    await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook1",
        "events": ["source.ingested"],
    }, headers=headers)
    await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook2",
        "events": ["chat.message"],
    }, headers=headers)

    resp = await client.get("/v1/webhooks", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Secret should NOT be exposed on list
    for wh in data:
        assert "secret" not in wh
        assert "has_secret" in wh


@pytest.mark.asyncio
async def test_get_webhook(client: AsyncClient):
    ctx = await _setup(client, "wh-get")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["source.ingested"],
    }, headers=ctx["headers"])
    wh_id = resp.json()["id"]

    resp = await client.get(f"/v1/webhooks/{wh_id}", headers=ctx["headers"])
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com/hook"
    # Secret should NOT be exposed on get
    assert "secret" not in resp.json()


@pytest.mark.asyncio
async def test_delete_webhook(client: AsyncClient):
    ctx = await _setup(client, "wh-delete")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["source.ingested"],
    }, headers=ctx["headers"])
    wh_id = resp.json()["id"]

    resp = await client.delete(f"/v1/webhooks/{wh_id}", headers=ctx["headers"])
    assert resp.status_code == 204

    # Should be gone
    resp = await client.get(f"/v1/webhooks/{wh_id}", headers=ctx["headers"])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_invalid_events(client: AsyncClient):
    ctx = await _setup(client, "wh-invalid-evt")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["invalid.event"],
    }, headers=ctx["headers"])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_webhook_dispatch(client: AsyncClient, session):
    """Test that dispatch_webhook_event sends correctly signed HTTP POST."""
    from app.models.webhook import Webhook
    from app.services.webhook_dispatch import dispatch_webhook_event

    # Create a tenant + webhook directly in DB
    ctx = await _setup(client, "wh-dispatch")
    tenant_id = ctx["tenant"]["id"]
    secret = "test-secret-123"  # noqa: S105

    wh = Webhook(
        tenant_id=uuid.UUID(tenant_id),
        url="https://example.com/dispatch-test",
        secret=secret,
        events=json.dumps(["source.ingested"]),
        description="dispatch test",
    )
    session.add(wh)
    await session.commit()

    mock_response = AsyncMock()
    mock_response.is_success = True
    mock_response.status_code = 200

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    payload = {"source_id": "abc", "chunk_count": 5}

    dispatch_target = "app.services.webhook_dispatch.httpx.AsyncClient"
    with patch(dispatch_target, return_value=mock_client_instance):
        await dispatch_webhook_event(session, tenant_id, "source.ingested", payload)

    # Verify the POST was called
    mock_client_instance.post.assert_called_once()
    call_kwargs = mock_client_instance.post.call_args

    # Verify HMAC signature
    body = json.dumps(payload, default=str)
    expected_sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert call_kwargs.kwargs["headers"]["X-MiniRAG-Signature"] == expected_sig
    assert call_kwargs.kwargs["headers"]["X-MiniRAG-Event"] == "source.ingested"


@pytest.mark.asyncio
async def test_webhook_dispatch_failure_no_propagation(client: AsyncClient, session):
    """Dispatch failures must not raise exceptions."""
    from app.models.webhook import Webhook
    from app.services.webhook_dispatch import dispatch_webhook_event

    ctx = await _setup(client, "wh-no-propagate")
    tenant_id = ctx["tenant"]["id"]

    wh = Webhook(
        tenant_id=uuid.UUID(tenant_id),
        url="https://example.com/fail",
        secret="secret",  # noqa: S106
        events=json.dumps(["source.failed"]),
    )
    session.add(wh)
    await session.commit()

    # Make httpx raise an exception
    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    dispatch_target = "app.services.webhook_dispatch.httpx.AsyncClient"
    with patch(dispatch_target, return_value=mock_client_instance):
        # Should not raise
        await dispatch_webhook_event(session, tenant_id, "source.failed", {"error": "boom"})


@pytest.mark.asyncio
async def test_webhook_test_ping(client: AsyncClient):
    """POST /v1/webhooks/{id}/test sends a signed ping."""
    ctx = await _setup(client, "wh-ping")
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/ping-target",
        "events": ["source.ingested"],
        "secret": "ping-secret",
    }, headers=ctx["headers"])
    wh_id = resp.json()["id"]

    mock_response = AsyncMock()
    mock_response.is_success = True
    mock_response.status_code = 200

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("app.api.v1.webhooks.httpx.AsyncClient", return_value=mock_client_instance):
        resp = await client.post(f"/v1/webhooks/{wh_id}/test", headers=ctx["headers"])

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status_code"] == 200

    # Verify the payload was a test ping
    call_kwargs = mock_client_instance.post.call_args
    sent_body = json.loads(call_kwargs.kwargs["content"])
    assert sent_body["event"] == "test.ping"
    assert sent_body["webhook_id"] == wh_id


@pytest.mark.asyncio
async def test_webhook_tenant_isolation(client: AsyncClient):
    """Tenant A's webhooks are invisible to tenant B."""
    ctx_a = await _setup(client, "wh-iso-a")
    ctx_b = await _setup(client, "wh-iso-b")

    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook-a",
        "events": ["source.ingested"],
    }, headers=ctx_a["headers"])
    wh_id = resp.json()["id"]

    # Tenant B cannot see tenant A's webhook
    resp = await client.get(f"/v1/webhooks/{wh_id}", headers=ctx_b["headers"])
    assert resp.status_code == 404

    # Tenant B's list is empty
    resp = await client.get("/v1/webhooks", headers=ctx_b["headers"])
    assert resp.status_code == 200
    assert len(resp.json()) == 0
