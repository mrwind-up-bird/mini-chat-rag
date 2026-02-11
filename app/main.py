"""FastAPI application entrypoint."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import v1_router
from app.core.config import get_settings
from app.core.database import init_db

DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: ensure tables exist (use Alembic in production)
    await init_db()
    yield
    # Shutdown: nothing to clean up yet


app = FastAPI(
    title="MiniRAG",
    version="0.1.1",
    description="Modular, provider-agnostic RAG platform",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ───────────────────────────────────────────────
app.include_router(v1_router)


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    return {"status": "ok"}


# ── Dashboard static files ───────────────────────────────────
if os.path.isdir(DASHBOARD_DIR):
    app.mount(
        "/dashboard/css",
        StaticFiles(directory=os.path.join(DASHBOARD_DIR, "css")),
        name="dashboard-css",
    )
    app.mount(
        "/dashboard/js",
        StaticFiles(directory=os.path.join(DASHBOARD_DIR, "js")),
        name="dashboard-js",
    )
    app.mount(
        "/dashboard/widget",
        StaticFiles(directory=os.path.join(DASHBOARD_DIR, "widget")),
        name="dashboard-widget",
    )

    @app.get("/dashboard/{path:path}", include_in_schema=False)
    async def dashboard_spa(request: Request, path: str = "") -> FileResponse:
        """Serve the SPA index.html for all dashboard routes."""
        # If requesting a specific file that exists, serve it
        file_path = os.path.join(DASHBOARD_DIR, path)
        if path and os.path.isfile(file_path):
            return FileResponse(file_path)
        # Otherwise serve index.html (SPA routing)
        return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_root() -> FileResponse:
        return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))
