"""System health endpoint — checks connectivity to all backing services."""

import platform
import sys
import time

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.config import get_settings
from app.models.bot_profile import BotProfile
from app.models.chat import Chat
from app.models.chunk import Chunk
from app.models.message import Message
from app.models.source import Source, SourceStatus
from app.models.usage_event import UsageEvent

router = APIRouter(prefix="/system", tags=["system"])

settings = get_settings()
_start_time = time.time()


class ServiceHealth(BaseModel):
    status: str  # "ok" or "error"
    detail: str | None = None
    version: str | None = None
    latency_ms: int | None = None


class HealthResponse(BaseModel):
    status: str
    postgres: ServiceHealth
    qdrant: ServiceHealth
    redis: ServiceHealth


@router.get("/health", response_model=HealthResponse)
async def system_health(session: Session) -> HealthResponse:
    """Check connectivity to Postgres, Qdrant, and Redis."""
    pg = await _check_postgres(session)
    qd = await _check_qdrant()
    rd = await _check_redis()

    overall = "ok" if all(s.status == "ok" for s in (pg, qd, rd)) else "degraded"
    return HealthResponse(status=overall, postgres=pg, qdrant=qd, redis=rd)


class BotSourceInfo(BaseModel):
    bot_profile_id: str
    bot_name: str
    model: str
    is_active: bool
    source_count: int
    ready_sources: int
    total_chunks: int


class DetailedHealthResponse(BaseModel):
    status: str
    uptime_seconds: int
    python_version: str
    platform: str

    # Service health
    postgres: ServiceHealth
    qdrant: ServiceHealth
    redis: ServiceHealth

    # Database stats
    db_stats: dict

    # Qdrant stats
    qdrant_stats: dict

    # Redis stats
    redis_stats: dict

    # Per-bot source breakdown
    bot_sources: list[BotSourceInfo]

    # Config (safe subset)
    config: dict


@router.get("/health/detailed")
async def system_health_detailed(auth: Auth, session: Session) -> DetailedHealthResponse:
    """Detailed system health with service versions, stats, and per-bot breakdown."""
    pg = await _check_postgres(session)
    qd = await _check_qdrant()
    rd = await _check_redis()

    overall = "ok" if all(s.status == "ok" for s in (pg, qd, rd)) else "degraded"

    db_stats = await _get_db_stats(session, auth.tenant_id)
    qdrant_stats = await _get_qdrant_stats()
    redis_stats = await _get_redis_stats()
    bot_sources = await _get_bot_sources(session, auth.tenant_id)

    return DetailedHealthResponse(
        status=overall,
        uptime_seconds=int(time.time() - _start_time),
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        postgres=pg,
        qdrant=qd,
        redis=rd,
        db_stats=db_stats,
        qdrant_stats=qdrant_stats,
        redis_stats=redis_stats,
        bot_sources=bot_sources,
        config={
            "database_url": _mask_url(settings.database_url),
            "redis_url": _mask_url(settings.redis_url),
            "qdrant_url": settings.qdrant_url,
            "encryption_configured": bool(settings.encryption_key),
            "jwt_configured": bool(settings.jwt_secret_key),
            "jwt_expire_minutes": settings.jwt_expire_minutes,
            "default_llm_model": settings.default_llm_model,
            "default_embedding_model": settings.default_embedding_model,
            "cors_origins": settings.allowed_origins,
        },
    )


def _mask_url(url: str) -> str:
    """Mask credentials in database/redis URLs."""
    if "://" not in url:
        return url
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.password:
            masked = parsed._replace(
                netloc=f"{parsed.username}:***@{parsed.hostname}"
                + (f":{parsed.port}" if parsed.port else "")
            )
            return urlunparse(masked)
    except Exception:
        pass
    return url.split("@")[-1] if "@" in url else url


async def _check_postgres(session) -> ServiceHealth:
    try:
        t0 = time.monotonic()
        await session.execute(text("SELECT 1"))
        latency = int((time.monotonic() - t0) * 1000)
        # Try to get version (works on PostgreSQL, may fail on SQLite)
        version_short = None
        try:
            result = await session.execute(text("SELECT version()"))
            version_str = result.scalar_one_or_none() or ""
            version_short = version_str.split(",")[0] if version_str else None
        except Exception:
            pass
        return ServiceHealth(status="ok", version=version_short, latency_ms=latency)
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])


async def _check_qdrant() -> ServiceHealth:
    try:
        import httpx
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.qdrant_url}/healthz")
            latency = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                # Try to get version from telemetry
                version = None
                try:
                    tel = await client.get(f"{settings.qdrant_url}/telemetry")
                    if tel.status_code == 200:
                        version = tel.json().get("result", {}).get("app", {}).get("version")
                except Exception:
                    pass
                return ServiceHealth(status="ok", version=version, latency_ms=latency)
            return ServiceHealth(
                status="error", detail=f"HTTP {resp.status_code}", latency_ms=latency,
            )
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])


async def _check_redis() -> ServiceHealth:
    try:
        from redis.asyncio import from_url
        t0 = time.monotonic()
        redis = from_url(settings.redis_url, decode_responses=True)
        pong = await redis.ping()
        latency = int((time.monotonic() - t0) * 1000)
        # Get Redis version
        info = await redis.info("server")
        version = info.get("redis_version")
        await redis.aclose()
        return ServiceHealth(
            status="ok" if pong else "error",
            version=f"Redis {version}" if version else None,
            latency_ms=latency,
        )
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])


async def _get_db_stats(session, tenant_id) -> dict:
    """Gather database statistics for the tenant."""
    try:
        # Source counts by status
        status_stmt = (
            select(Source.status, func.count())
            .where(Source.tenant_id == tenant_id, Source.is_active == True)  # noqa: E712
            .group_by(Source.status)
        )
        status_result = await session.execute(status_stmt)
        source_by_status = {str(row[0]): row[1] for row in status_result.all()}

        # Total chunks
        chunk_count = (await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.tenant_id == tenant_id)
        )).scalar_one()

        # Total messages
        message_count = (await session.execute(
            select(func.count()).select_from(Message).where(Message.tenant_id == tenant_id)
        )).scalar_one()

        # Total chats
        chat_count = (await session.execute(
            select(func.count()).select_from(Chat).where(Chat.tenant_id == tenant_id)
        )).scalar_one()

        # Total usage events
        usage_count = (await session.execute(
            select(func.count()).select_from(UsageEvent).where(UsageEvent.tenant_id == tenant_id)
        )).scalar_one()

        # Total tokens consumed
        token_sums = (await session.execute(
            select(
                func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
            ).where(UsageEvent.tenant_id == tenant_id)
        )).one()

        return {
            "sources_by_status": source_by_status,
            "total_chunks": chunk_count,
            "total_messages": message_count,
            "total_chats": chat_count,
            "total_usage_events": usage_count,
            "total_prompt_tokens": token_sums[0],
            "total_completion_tokens": token_sums[1],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def _get_qdrant_stats() -> dict:
    """Get Qdrant collection statistics."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.qdrant_url}/collections/minirag_chunks"
            )
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                return {
                    "collection": "minirag_chunks",
                    "vectors_count": data.get("vectors_count", 0),
                    "points_count": data.get("points_count", 0),
                    "segments_count": data.get("segments_count", 0),
                    "status": data.get("status", "unknown"),
                    "disk_data_size_mb": round(
                        data.get("disk_data_size", 0) / (1024 * 1024), 2
                    ),
                    "ram_data_size_mb": round(
                        data.get("ram_data_size", 0) / (1024 * 1024), 2
                    ),
                }
            return {"error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def _get_redis_stats() -> dict:
    """Get Redis memory and client statistics."""
    try:
        from redis.asyncio import from_url
        redis = from_url(settings.redis_url, decode_responses=True)
        info = await redis.info("memory")
        clients_info = await redis.info("clients")
        server_info = await redis.info("server")
        db_size = await redis.dbsize()
        await redis.aclose()

        return {
            "used_memory_human": info.get("used_memory_human", "unknown"),
            "used_memory_peak_human": info.get("used_memory_peak_human", "unknown"),
            "connected_clients": clients_info.get("connected_clients", 0),
            "uptime_seconds": server_info.get("uptime_in_seconds", 0),
            "db_keys": db_size,
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def _get_bot_sources(session, tenant_id) -> list[BotSourceInfo]:
    """Per-bot source breakdown."""
    try:
        # Get all active bots
        bot_stmt = (
            select(BotProfile)
            .where(BotProfile.tenant_id == tenant_id)
            .order_by(BotProfile.created_at.desc())  # type: ignore[union-attr]
        )
        bots = (await session.execute(bot_stmt)).scalars().all()

        result = []
        for bot in bots:
            # Count sources and their statuses
            src_stmt = (
                select(func.count())
                .select_from(Source)
                .where(
                    Source.tenant_id == tenant_id,
                    Source.bot_profile_id == bot.id,
                    Source.is_active == True,  # noqa: E712
                )
            )
            source_count = (await session.execute(src_stmt)).scalar_one()

            ready_stmt = (
                select(func.count())
                .select_from(Source)
                .where(
                    Source.tenant_id == tenant_id,
                    Source.bot_profile_id == bot.id,
                    Source.is_active == True,  # noqa: E712
                    Source.status == SourceStatus.READY,
                )
            )
            ready_count = (await session.execute(ready_stmt)).scalar_one()

            chunk_stmt = (
                select(func.coalesce(func.sum(Source.chunk_count), 0))
                .where(
                    Source.tenant_id == tenant_id,
                    Source.bot_profile_id == bot.id,
                    Source.is_active == True,  # noqa: E712
                )
            )
            total_chunks = (await session.execute(chunk_stmt)).scalar_one()

            result.append(BotSourceInfo(
                bot_profile_id=str(bot.id),
                bot_name=bot.name,
                model=bot.model,
                is_active=bot.is_active,
                source_count=source_count,
                ready_sources=ready_count,
                total_chunks=total_chunks,
            ))

        return result
    except Exception:
        return []
