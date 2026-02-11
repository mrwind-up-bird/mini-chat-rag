"""Tests for the scheduled refresh logic."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.models.base import utcnow
from app.models.source import Source, SourceStatus
from app.workers.ingest import ingest_source
from app.workers.refresh import check_refresh_schedules


async def _setup_source(
    client: AsyncClient,
    slug: str,
    *,
    refresh_schedule: str | None = None,
    source_type: str = "text",
    content: str = "Some knowledge base content for testing purposes.",
    config: dict | None = None,
) -> dict:
    """Bootstrap tenant + bot profile + source with optional refresh schedule."""
    resp = await client.post(
        "/v1/tenants",
        json={
            "tenant_name": "Refresh Co",
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

    body: dict = {
        "bot_profile_id": profile_id,
        "name": "Scheduled Source",
        "source_type": source_type,
        "content": content,
    }
    if config is not None:
        body["config"] = config
    if refresh_schedule is not None:
        body["refresh_schedule"] = refresh_schedule

    resp = await client.post("/v1/sources", json=body, headers=headers)
    source = resp.json()

    return {
        "headers": headers,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "source_id": source["id"],
    }


def _mock_embedding(n_texts: int) -> list[list[float]]:
    return [[0.1] * 1536 for _ in range(n_texts)]


async def _ingest_source(source_id: str, tenant_id: str, test_session_factory) -> dict:
    """Helper to run ingest with all external deps mocked."""
    mock_embed = AsyncMock(side_effect=lambda texts, **kw: _mock_embedding(len(texts)))
    with (
        patch("app.workers.ingest.async_session_factory", test_session_factory),
        patch("app.workers.ingest.embed_texts", mock_embed),
        patch("app.workers.ingest.ensure_collection", AsyncMock()),
        patch("app.workers.ingest.upsert_chunks", AsyncMock()),
        patch("app.workers.ingest.delete_by_source", AsyncMock()),
    ):
        return await ingest_source({}, source_id=source_id, tenant_id=tenant_id)


async def _run_scheduler(test_session_factory) -> tuple[dict, AsyncMock]:
    """Run check_refresh_schedules with mocked DB and redis pool."""
    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock()

    with patch("app.workers.refresh.async_session_factory", test_session_factory):
        result = await check_refresh_schedules({"redis": mock_pool})

    return result, mock_pool


@pytest.mark.asyncio
async def test_hourly_source_eligible(
    client: AsyncClient, session, test_session_factory
):
    """Source with hourly schedule and last_refreshed_at >60min ago should be enqueued."""
    ctx = await _setup_source(client, "refresh-hourly", refresh_schedule="hourly")

    # Run initial ingest so status=ready and last_refreshed_at is set
    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    # Backdate last_refreshed_at to 2 hours ago
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.last_refreshed_at = utcnow() - timedelta(hours=2)
    session.add(source)
    await session.commit()

    result, mock_pool = await _run_scheduler(test_session_factory)

    assert result["enqueued"] >= 1
    mock_pool.enqueue_job.assert_called()


@pytest.mark.asyncio
async def test_hourly_source_not_due(
    client: AsyncClient, session, test_session_factory
):
    """Source refreshed 30min ago with hourly schedule should NOT be enqueued."""
    ctx = await _setup_source(client, "refresh-notdue", refresh_schedule="hourly")

    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    # Set last_refreshed_at to 30 minutes ago (within hourly window)
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.last_refreshed_at = utcnow() - timedelta(minutes=30)
    session.add(source)
    await session.commit()

    result, mock_pool = await _run_scheduler(test_session_factory)

    # This specific source should NOT be enqueued
    for call in mock_pool.enqueue_job.call_args_list:
        assert call.kwargs.get("source_id") != ctx["source_id"]


@pytest.mark.asyncio
async def test_daily_source_eligible(
    client: AsyncClient, session, test_session_factory
):
    """Source with daily schedule and last_refreshed_at >24h ago should be enqueued."""
    ctx = await _setup_source(client, "refresh-daily", refresh_schedule="daily")

    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    # Backdate last_refreshed_at to 25 hours ago
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.last_refreshed_at = utcnow() - timedelta(hours=25)
    session.add(source)
    await session.commit()

    result, _ = await _run_scheduler(test_session_factory)

    assert result["enqueued"] >= 1


@pytest.mark.asyncio
async def test_first_refresh_after_ingest(
    client: AsyncClient, session, test_session_factory
):
    """Source with schedule, last_refreshed_at=None, status=ready should be enqueued."""
    ctx = await _setup_source(client, "refresh-first", refresh_schedule="hourly")

    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    # Clear last_refreshed_at but keep status=ready (simulates upgrade scenario)
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.last_refreshed_at = None
    session.add(source)
    await session.commit()

    result, _ = await _run_scheduler(test_session_factory)

    assert result["enqueued"] >= 1


@pytest.mark.asyncio
async def test_processing_source_skipped(
    client: AsyncClient, session, test_session_factory
):
    """Source already processing should NOT be enqueued."""
    ctx = await _setup_source(
        client, "refresh-processing", refresh_schedule="hourly"
    )

    # Set status to processing
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.status = SourceStatus.PROCESSING
    source.last_refreshed_at = utcnow() - timedelta(hours=2)
    session.add(source)
    await session.commit()

    _, mock_pool = await _run_scheduler(test_session_factory)

    # Verify this source was not enqueued
    for call in mock_pool.enqueue_job.call_args_list:
        assert call.kwargs.get("source_id") != ctx["source_id"]


@pytest.mark.asyncio
async def test_inactive_source_skipped(
    client: AsyncClient, session, test_session_factory
):
    """Inactive source should NOT be enqueued."""
    ctx = await _setup_source(
        client, "refresh-inactive", refresh_schedule="hourly"
    )

    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    # Soft-delete the source
    stmt = select(Source).where(Source.id == uuid.UUID(ctx["source_id"]))
    res = await session.execute(stmt)
    source = res.scalar_one()
    source.is_active = False
    source.last_refreshed_at = utcnow() - timedelta(hours=2)
    session.add(source)
    await session.commit()

    _, mock_pool = await _run_scheduler(test_session_factory)

    for call in mock_pool.enqueue_job.call_args_list:
        assert call.kwargs.get("source_id") != ctx["source_id"]


@pytest.mark.asyncio
async def test_last_refreshed_at_updated(client: AsyncClient, test_session_factory):
    """After successful ingest, last_refreshed_at should be set."""
    ctx = await _setup_source(client, "refresh-updated")

    result = await _ingest_source(
        ctx["source_id"], ctx["tenant_id"], test_session_factory
    )
    assert "error" not in result

    resp = await client.get(
        f"/v1/sources/{ctx['source_id']}", headers=ctx["headers"]
    )
    source_data = resp.json()
    assert source_data["status"] == "ready"
    assert source_data["last_refreshed_at"] is not None
