"""Periodic job — find sources due for scheduled re-ingestion and enqueue them."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlmodel import select

from app.core.database import async_session_factory
from app.models.base import utcnow
from app.models.source import RefreshSchedule, Source, SourceStatus

logger = logging.getLogger(__name__)

_INTERVALS: dict[str, timedelta] = {
    RefreshSchedule.HOURLY: timedelta(hours=1),
    RefreshSchedule.DAILY: timedelta(days=1),
    RefreshSchedule.WEEKLY: timedelta(weeks=1),
}


async def check_refresh_schedules(ctx: dict) -> dict:
    """Periodic job: find sources due for refresh and enqueue ingest jobs.

    When run by ARQ, ``ctx["redis"]`` is the worker's ArqRedis pool.
    For tests, callers inject a mock pool via ``ctx["redis"]``.
    """
    now = utcnow()
    enqueued = 0

    async with async_session_factory() as session:
        stmt = select(Source).where(
            Source.refresh_schedule.is_not(None),  # type: ignore[union-attr]
            Source.refresh_schedule != RefreshSchedule.NONE,
            Source.status != SourceStatus.PROCESSING,
            Source.is_active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        sources = list(result.scalars().all())

    eligible: list[Source] = []
    for source in sources:
        interval = _INTERVALS.get(source.refresh_schedule)  # type: ignore[arg-type]
        if interval is None:
            continue

        if source.last_refreshed_at is None:
            # First refresh: only enqueue if initial ingest is done
            if source.status == SourceStatus.READY:
                eligible.append(source)
        elif (now - source.last_refreshed_at) >= interval:
            eligible.append(source)

    if not eligible:
        logger.info("Refresh scheduler: no sources due for refresh")
        return {"enqueued": 0}

    redis = ctx["redis"]
    for source in eligible:
        await redis.enqueue_job(
            "ingest_source",
            source_id=str(source.id),
            tenant_id=str(source.tenant_id),
        )
        enqueued += 1
        logger.info("Enqueued refresh for source %s", source.id)

    logger.info("Refresh scheduler: enqueued %d sources", enqueued)
    return {"enqueued": enqueued}
