# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable with dev deps)
pip install -e ".[dev]"

# Run API server (dev, auto-reload)
uvicorn app.main:app --reload

# Run ARQ worker
python -m app.workers.main

# Infrastructure (Postgres, Qdrant, Redis)
docker compose up -d postgres qdrant redis

# Tests (SQLite in-memory, no Docker needed)
pytest tests/ -v
pytest tests/test_chat.py -v                           # single file
pytest tests/test_chat.py::test_chat_new_conversation -v  # single test

# Lint & format
ruff check .
ruff format .

# Postman collection (28 requests, runs sequentially)
newman run postman/MiniRAG.postman_collection.json
```

## Architecture

Multi-tenant RAG platform: FastAPI + SQLModel (async) + PostgreSQL + Qdrant + Redis/ARQ + LiteLLM.

**Request flow**: Bearer token → `app/api/deps.py:get_auth_context()` resolves to `AuthContext(tenant_id, user_id, role)` → route handler → service layer. Auth dispatch: tokens with dots go JWT path, others go API token path (SHA-256 lookup).

**RAG pipeline** (`POST /v1/chat`): `orchestrator.run_chat_turn()` → embed query → Qdrant search (top_k=5, filtered by tenant_id + bot_profile_id) → build messages (system prompt + context + last 10 turns) → LiteLLM acompletion → save message + usage event.

**Ingestion pipeline** (`POST /v1/sources/{id}/ingest` → 202): Enqueues ARQ job → `workers/ingest.py:ingest_source()` → extract content → chunk (512/64) → embed → upsert to Qdrant → update source status.

**Dashboard** (`dashboard/`): No-build SPA (Alpine.js + Tailwind CDN). Served by FastAPI at `/dashboard` via static files + index.html catch-all. Embeddable chat widget at `dashboard/widget/`.

## Multi-Tenancy

Every table has `tenant_id`. All queries must filter by `auth.tenant_id`. Cross-tenant FK references (e.g., bot_profile_id on Source) must be validated before creation. Qdrant uses a single collection `minirag_chunks` with tenant isolation via payload filters.

## Critical Patterns

**Datetime handling**: ALWAYS use `from app.models.base import utcnow` for DB timestamps. Returns naive UTC datetime (no tzinfo). Using `datetime.now(timezone.utc)` directly breaks asyncpg with `TIMESTAMP WITHOUT TIME ZONE` columns.

**Auth dependency**: `Auth = Annotated[AuthContext, Depends(get_auth_context)]` — use in route signatures. `Session = Annotated[AsyncSession, Depends(get_session)]`.

**Encrypted credentials**: BotProfile stores Fernet-encrypted JSON in `encrypted_credentials`. Read schema exposes `has_credentials: bool` instead of ciphertext. Decrypt with `decrypt_value()` from `core/security.py`.

**Source config**: Stored as JSON text (`sa_column=Column(Text)`). Serialize with `json.dumps()` on write, `json.loads()` on read in route handlers.

**Enums**: Use `StrEnum` for all role/type/status enums — serializes cleanly with Pydantic v2.

**Error codes**: 401 (unauthenticated), 403 (forbidden), 404 (not found), 409 (conflict/duplicate), 422 (validation — use `HTTP_422_UNPROCESSABLE_CONTENT`, not the deprecated `ENTITY` variant).

## Test Infrastructure

Tests use SQLite in-memory (`aiosqlite`) — no Docker required. `tests/conftest.py` provides:
- `session` fixture: per-test async session with rollback
- `client` fixture: HTTPX AsyncClient with `get_session()` dependency override

**Mocking patterns**:
- Worker tests: patch `app.workers.ingest.async_session_factory` with `test_session_factory`
- LLM: patch `litellm.acompletion` and `litellm.aembedding`
- Vector search: patch `app.services.vector_store.search_chunks`

**Chat test ordering**: Load history BEFORE saving new user message to avoid duplication in LLM context.

## Environment

Required env vars (see `.env.example`): `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`, `ENCRYPTION_KEY` (Fernet), `JWT_SECRET_KEY`. LLM provider keys (`OPENAI_API_KEY`, etc.) set in environment or per-bot via encrypted credentials.
