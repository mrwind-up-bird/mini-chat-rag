"""System health endpoint — checks connectivity to all backing services."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import Session
from app.core.config import get_settings

router = APIRouter(prefix="/system", tags=["system"])

settings = get_settings()


class ServiceHealth(BaseModel):
    status: str  # "ok" or "error"
    detail: str | None = None


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


async def _check_postgres(session) -> ServiceHealth:
    try:
        await session.execute(text("SELECT 1"))
        return ServiceHealth(status="ok")
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])


async def _check_qdrant() -> ServiceHealth:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.qdrant_url}/healthz")
            if resp.status_code == 200:
                return ServiceHealth(status="ok")
            return ServiceHealth(status="error", detail=f"HTTP {resp.status_code}")
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])


async def _check_redis() -> ServiceHealth:
    try:
        from redis.asyncio import from_url
        redis = from_url(settings.redis_url, decode_responses=True)
        pong = await redis.ping()
        await redis.aclose()
        return ServiceHealth(status="ok" if pong else "error")
    except Exception as exc:
        return ServiceHealth(status="error", detail=str(exc)[:200])
