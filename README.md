# MiniRAG

A modular, provider-agnostic Retrieval-Augmented Generation (RAG) platform with multi-tenancy. API-first design for building knowledge-powered chatbots.

## Architecture

```
                    +------------------+
                    |   API Gateway    |  FastAPI + Bearer Auth
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     |  Bot CRUD  |  | Source CRUD |  |  Chat API   |
     +------------+  +------+------+  +----+--------+
                            |              |
                     +------v------+  +----v--------+
                     |   Worker    |  | Orchestrator|
                     | (ARQ/Redis) |  |  (RAG Brain)|
                     +------+------+  +----+--------+
                            |              |
              +-------------+--------------+----------+
              |                            |          |
     +--------v--------+         +--------v---+  +---v--------+
     | PostgreSQL       |         |  Qdrant    |  |  LiteLLM   |
     | (metadata, auth) |         | (vectors)  |  | (LLM proxy)|
     +------------------+         +------------+  +------------+
```

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI (async, Pydantic v2) |
| Metadata DB | PostgreSQL via SQLModel (async SQLAlchemy) |
| Vector DB | Qdrant |
| LLM abstraction | LiteLLM (OpenAI, Anthropic, Gemini interchangeable) |
| Task queue | Redis + ARQ |
| Auth | Argon2 passwords, SHA-256 API tokens, Fernet field encryption, JWT |
| Containerization | Docker & Docker Compose |

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for infrastructure services)

### Local Development

```bash
# Clone and set up
git clone <repo-url> && cd mini-chat-rag
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your settings (encryption keys, DB URLs, etc.)

# Start infrastructure
docker compose up -d postgres qdrant redis

# Run the API
uvicorn app.main:app --reload

# Run the worker (in a separate terminal)
python -m app.workers.main
```

### Run Tests

Tests use SQLite in-memory (no Docker needed):

```bash
pytest tests/ -v
```

## API Overview

### Bootstrap a Tenant

```bash
curl -X POST http://localhost:8000/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "Acme Corp", "tenant_slug": "acme",
       "owner_email": "admin@acme.com", "owner_password": "supersecret123"}'
```

Returns a one-time API token. Use it for all subsequent requests:

```bash
export TOKEN="<raw_token from response>"
```

### Create a Bot Profile

```bash
curl -X POST http://localhost:8000/v1/bot-profiles \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Support Bot", "system_prompt": "You help customers.",
       "model": "gpt-4o-mini"}'
```

### Add a Knowledge Source

```bash
curl -X POST http://localhost:8000/v1/sources \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"bot_profile_id": "<profile_id>", "name": "FAQ",
       "source_type": "text", "content": "Your knowledge base text..."}'
```

### Ingest the Source

```bash
curl -X POST http://localhost:8000/v1/sources/<source_id>/ingest \
  -H "Authorization: Bearer $TOKEN"
```

### Chat

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"bot_profile_id": "<profile_id>", "message": "What is MiniRAG?"}'
```

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/tenants` | No | Bootstrap tenant + owner + API token |
| `GET` | `/v1/tenants/me` | Yes | Get current tenant info |
| `POST` | `/v1/api-tokens` | Yes | Create new API token |
| `GET` | `/v1/api-tokens` | Yes | List API tokens |
| `DELETE` | `/v1/api-tokens/{id}` | Yes | Revoke token |
| `POST` | `/v1/bot-profiles` | Yes | Create bot profile |
| `GET` | `/v1/bot-profiles` | Yes | List bot profiles |
| `GET` | `/v1/bot-profiles/{id}` | Yes | Get bot profile |
| `PATCH` | `/v1/bot-profiles/{id}` | Yes | Update bot profile |
| `DELETE` | `/v1/bot-profiles/{id}` | Yes | Deactivate bot profile |
| `POST` | `/v1/sources` | Yes | Create source |
| `GET` | `/v1/sources` | Yes | List sources |
| `GET` | `/v1/sources/{id}` | Yes | Get source |
| `PATCH` | `/v1/sources/{id}` | Yes | Update source |
| `DELETE` | `/v1/sources/{id}` | Yes | Deactivate source |
| `POST` | `/v1/sources/{id}/ingest` | Yes | Trigger ingestion |
| `POST` | `/v1/chat` | Yes | Send message (RAG) |
| `GET` | `/v1/chat/{id}` | Yes | Get chat metadata |
| `GET` | `/v1/chat/{id}/messages` | Yes | Get chat history |

## Data Model

10 tables with strict tenant isolation (`tenant_id` on every entity):

- **Tenant** - Top-level organization boundary
- **User** - Belongs to tenant (owner/admin/member roles)
- **ApiToken** - Bearer tokens (SHA-256 hashed, shown once at creation)
- **BotProfile** - AI assistant config (model, prompt, Fernet-encrypted credentials)
- **Source** - Knowledge source (text/upload/url) with ingestion status
- **Document** - Raw content extracted from a source
- **Chunk** - Indexed text segment with Qdrant vector reference
- **Chat** - Conversation session with token counters
- **Message** - Single turn (user/assistant) with token usage
- **UsageEvent** - Per-request LLM token tracking

## Security

- API tokens stored as SHA-256 hashes (never plaintext)
- Passwords hashed with Argon2
- Provider API keys encrypted at rest with Fernet
- All queries automatically scoped by `tenant_id`
- Cross-tenant FK references validated before creation
- No secrets in code or logs; everything via `.env`

## Project Structure

```
app/
  main.py                  # FastAPI entrypoint
  core/
    config.py              # Settings from .env
    database.py            # Async engine + session factory
    security.py            # Encryption, hashing, JWT
  models/                  # SQLModel tables + Pydantic schemas
  api/
    deps.py                # Auth dependencies
    v1/                    # Versioned route modules
  services/
    chunking.py            # Text normalization + splitting
    embedding.py           # LiteLLM embedding wrapper
    vector_store.py        # Qdrant operations
    orchestrator.py        # RAG pipeline
  workers/
    main.py                # ARQ worker config
    ingest.py              # Ingestion task
tests/                     # 31 async integration tests
docker-compose.yml         # Postgres, Qdrant, Redis, web, worker
Dockerfile                 # Multi-stage (web + worker targets)
```

## License

See [LICENSE](LICENSE).
