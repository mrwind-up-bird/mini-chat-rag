"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.api.v1 import v1_router
from app.core.database import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: ensure tables exist (use Alembic in production)
    await init_db()
    yield
    # Shutdown: nothing to clean up yet


app = FastAPI(
    title="MiniRAG",
    version="0.1.0",
    description="Modular, provider-agnostic RAG platform",
    lifespan=lifespan,
)


app.include_router(v1_router)


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    return {"status": "ok"}
