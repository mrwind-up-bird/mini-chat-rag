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
| Dashboard | HTML + Tailwind CSS (CDN) + Alpine.js + Chart.js — no build step |

## Dashboard

MiniRAG includes a built-in admin dashboard served directly by FastAPI at `/dashboard`. No build step required.

- **Overview** — Service health, summary stats, quick actions
- **Bot Profiles** — CRUD with inline "Try It" chat
- **Sources** — Manage knowledge sources, trigger ingestion
- **Chat History** — Browse and view conversations
- **API Tokens** — Create/revoke tokens
- **Users** — Team management (owner/admin only)
- **Usage Analytics** — Token consumption charts by day and model
- **Settings** — Tenant info, system health

See [docs/admin-guide.md](docs/admin-guide.md) for a full walkthrough.

## Embeddable Chat Widget

Add a chat widget to any website with a single script tag:

```html
<script src="https://your-host/dashboard/widget/minirag-widget.js"
        data-bot-id="YOUR_BOT_PROFILE_ID"
        data-api-url="https://your-host"
        data-api-token="YOUR_API_TOKEN">
</script>
```

See [docs/widget-integration.md](docs/widget-integration.md) for configuration options and styling.

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
| `POST` | `/v1/auth/login` | No | Login with email + password, get JWT |
| `GET` | `/v1/auth/me` | Yes | Get current user + tenant |
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
| `GET` | `/v1/chat` | Yes | List chat sessions |
| `POST` | `/v1/chat` | Yes | Send message (RAG) |
| `GET` | `/v1/chat/{id}` | Yes | Get chat metadata |
| `GET` | `/v1/chat/{id}/messages` | Yes | Get chat history |
| `POST` | `/v1/users` | Yes | Create user (admin+) |
| `GET` | `/v1/users` | Yes | List users |
| `PATCH` | `/v1/users/{id}` | Yes | Update user (admin+) |
| `DELETE` | `/v1/users/{id}` | Yes | Deactivate user (admin+) |
| `GET` | `/v1/stats/overview` | Yes | Summary counts |
| `GET` | `/v1/stats/usage` | Yes | Token usage by day/model |
| `GET` | `/v1/system/health` | Yes | Service connectivity check |

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

## Testing with Postman

A complete Postman collection is included at `postman/MiniRAG.postman_collection.json` with 28 requests across 7 folders, each with automated test scripts.

### Import & Run

1. **Import**: Open Postman, click Import, and select `postman/MiniRAG.postman_collection.json`
2. **Run in order**: Execute the numbered folders sequentially (0-6) — test scripts auto-save IDs and tokens to collection variables

### Collection Structure

| Folder | Requests | What it tests |
|---|---|---|
| 0 — Health | 1 | Smoke test (no auth) |
| 1 — Tenant Bootstrap | 4 | Create tenant, duplicate slug 409, get tenant, unauthenticated 401 |
| 2 — API Tokens | 3 | Create, list, revoke |
| 3 — Bot Profiles | 6 | Create, create with credentials, list, get, update, 404 |
| 4 — Sources | 8 | Create, list, filter, get, update, ingest trigger, status poll, cross-tenant 422 |
| 5 — Chat (RAG) | 6 | New conversation, continue, get metadata, get history, invalid bot 404, invalid chat 404 |
| 6 — Cleanup | 2 | Soft-delete source and bot profile |

### Auto-Managed Variables

The collection uses these variables (auto-populated by test scripts):

| Variable | Set by | Used by |
|---|---|---|
| `base_url` | Pre-configured (`http://localhost:8000`) | All requests |
| `api_token` | Bootstrap Tenant | All authenticated requests (collection-level Bearer auth) |
| `tenant_id` | Bootstrap Tenant | Tenant assertions |
| `profile_id` | Create Bot Profile | Sources, Chat, Cleanup |
| `source_id` | Create Source | Ingest, Get/Update/Delete Source |
| `chat_id` | Start New Conversation | Continue, Get metadata/messages |
| `token_id` | Create API Token | Revoke API Token |

### Running with Newman (CLI)

```bash
# Install Newman
npm install -g newman

# Run the full collection
newman run postman/MiniRAG.postman_collection.json

# Run with a custom base URL
newman run postman/MiniRAG.postman_collection.json --env-var "base_url=http://localhost:9000"
```

### Prerequisites for Full Run

- API running (`uvicorn app.main:app --reload`)
- For folders 0-4: only needs Postgres (infrastructure services)
- For folder 5 (Chat): also needs Redis + worker + Qdrant + a configured LLM provider
- Run folder 4's "Trigger Ingestion" and wait for source status to become `ready` before running folder 5

## Project Structure

```
app/
  main.py                  # FastAPI entrypoint + CORS + static serving
  core/
    config.py              # Settings from .env
    database.py            # Async engine + session factory
    security.py            # Encryption, hashing, JWT
  models/                  # SQLModel tables + Pydantic schemas
  api/
    deps.py                # Auth dependencies
    v1/
      auth.py              # Login + /me endpoints
      tenants.py           # Tenant bootstrap
      api_tokens.py        # Token CRUD
      bot_profiles.py      # Bot profile CRUD
      sources.py           # Source CRUD + ingest trigger
      chat.py              # Chat API + list chats
      users.py             # Users CRUD
      stats.py             # Usage statistics
      system.py            # System health
  services/
    chunking.py            # Text normalization + splitting
    embedding.py           # LiteLLM embedding wrapper
    vector_store.py        # Qdrant operations
    orchestrator.py        # RAG pipeline
  workers/
    main.py                # ARQ worker config
    ingest.py              # Ingestion task
dashboard/
  index.html               # SPA dashboard (Alpine.js + Tailwind)
  css/app.css              # Custom styles
  js/api.js                # API client with auth
  js/app.js                # Alpine.js stores + helpers
  widget/
    minirag-widget.js      # Embeddable chat widget (Shadow DOM)
    minirag-widget.css     # Widget styles
docs/
  installation.md          # Setup & maintenance guide
  admin-guide.md           # Dashboard usage guide
  widget-integration.md    # Widget embedding guide
tests/                     # 31+ async integration tests
postman/                   # Postman collection (28 requests, auto-tests)
docker-compose.yml         # Postgres, Qdrant, Redis, web, worker
Dockerfile                 # Multi-stage (web + worker targets)
```

## Blog Drafts

Build notes and development logs are in the [`drafts/`](drafts/) directory.

## License

See [LICENSE](LICENSE).
