# Installation & Maintenance Guide

## Prerequisites

- **Python 3.11+** (3.12 recommended)
- **Docker & Docker Compose** (for PostgreSQL, Qdrant, Redis)
- **Node.js** (optional, for Newman CLI testing)

## Quick Start

### 1. Clone & Install

```bash
git clone <repo-url> && cd mini-chat-rag
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://minirag:changeme@localhost:5432/minirag` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `QDRANT_URL` | Qdrant REST URL | `http://localhost:6333` |
| `ENCRYPTION_KEY` | Fernet key for field encryption | *required* |
| `JWT_SECRET_KEY` | Secret for JWT signing | *required* |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_EXPIRE_MINUTES` | JWT token TTL | `60` |
| `DEFAULT_LLM_MODEL` | Default LLM model | `gpt-4o-mini` |
| `DEFAULT_EMBEDDING_MODEL` | Default embedding model | `text-embedding-3-small` |

**Generate keys:**

```bash
# Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3. Start Infrastructure

```bash
docker compose up -d postgres qdrant redis
```

### 4. Run the API

```bash
uvicorn app.main:app --reload
```

The API is now available at `http://localhost:8000`. Docs at `/docs`.

### 5. Run the Worker

In a separate terminal:

```bash
source .venv/bin/activate
python -m app.workers.main
```

### 6. Access the Dashboard

Open `http://localhost:8000/dashboard` in your browser.

## First-Run Bootstrap

Create your first tenant (no auth required):

```bash
curl -X POST http://localhost:8000/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "My Company",
    "tenant_slug": "my-company",
    "owner_email": "admin@example.com",
    "owner_password": "supersecret123"
  }'
```

This returns a one-time API token. You can now log into the dashboard with the email/password above.

## Docker Compose (Full Stack)

To run everything including the web server and worker:

```bash
docker compose up -d
```

Services:
- **postgres** — `localhost:5432`
- **qdrant** — `localhost:6333` (REST), `localhost:6334` (gRPC)
- **redis** — `localhost:6379`
- **web** — `localhost:8000` (API + Dashboard)
- **worker** — ARQ background worker

## Running Tests

Tests use SQLite in-memory (no Docker needed):

```bash
pytest tests/ -v
```

## Production Considerations

- Use Alembic for database migrations instead of `create_all`
- Set strong `ENCRYPTION_KEY` and `JWT_SECRET_KEY` values
- Configure CORS `allow_origins` to your domain (not `*`)
- Use a reverse proxy (nginx/Caddy) for TLS termination
- Set `JWT_EXPIRE_MINUTES` to an appropriate value
- Monitor with the `/v1/system/health` endpoint
