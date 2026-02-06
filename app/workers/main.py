"""ARQ worker entrypoint."""

import asyncio

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.ingest import ingest_source


def _redis_settings() -> RedisSettings:
    """Parse REDIS_URL into ARQ RedisSettings."""
    settings = get_settings()
    # redis://host:port/db
    url = settings.redis_url
    # Strip scheme
    rest = url.split("://", 1)[1] if "://" in url else url
    host_port, _, db = rest.partition("/")
    host, _, port = host_port.partition(":")
    return RedisSettings(
        host=host or "localhost",
        port=int(port) if port else 6379,
        database=int(db) if db else 0,
    )


async def startup(ctx: dict) -> None:
    """Called when the worker starts."""
    from app.core.database import init_db
    await init_db()


async def shutdown(ctx: dict) -> None:
    """Called when the worker shuts down."""


class WorkerSettings:
    """ARQ worker configuration."""
    functions = [ingest_source]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = 10
    job_timeout = 600  # 10 minutes per ingestion job


if __name__ == "__main__":
    from arq import run_worker
    asyncio.run(run_worker(WorkerSettings))  # type: ignore[arg-type]
