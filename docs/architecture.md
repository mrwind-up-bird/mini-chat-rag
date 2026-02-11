# Architecture

Comprehensive technical reference for the MiniRAG platform.

## System Overview

```mermaid
graph TB
    subgraph Clients
        Dashboard[Dashboard SPA]
        Widget[Chat Widget]
        API_Client[API Clients]
    end
    
    subgraph "FastAPI Application"
        Gateway[API Gateway<br/>Bearer Auth]
        subgraph "Route Handlers"
            Bots[/v1/bot-profiles/]
            Sources[/v1/sources/]
            Chat[/v1/chat/]
            Webhooks_R[/v1/webhooks/]
            Stats_R[/v1/stats/]
        end
    end
    
    subgraph "Service Layer"
        Orchestrator[RAG Orchestrator]
        ChunkSvc[Chunking Service]
        EmbedSvc[Embedding Service]
        VectorSvc[Vector Store Service]
        WebhookSvc[Webhook Dispatch]
    end
    
    subgraph "Background Workers"
        ARQ[ARQ Worker]
        IngestTask[Ingest Task]
        RefreshTask[Refresh Scheduler<br/>Cron: every 15min]
    end
    
    subgraph "Data Stores"
        PG[(PostgreSQL)]
        Qdrant[(Qdrant)]
        Redis[(Redis)]
    end
    
    subgraph "External"
        LLM_API[LLM Providers<br/>OpenAI / Anthropic / Google]
        ExtWebhooks[Webhook URLs]
    end
    
    Clients --> Gateway
    Gateway --> Bots & Sources & Chat & Webhooks_R & Stats_R
    
    Chat --> Orchestrator
    Orchestrator --> EmbedSvc --> LLM_API
    Orchestrator --> VectorSvc --> Qdrant
    Orchestrator --> LLM_API
    
    Sources -->|enqueue| Redis
    Redis --> ARQ
    ARQ --> IngestTask
    ARQ --> RefreshTask
    IngestTask --> ChunkSvc
    IngestTask --> EmbedSvc
    IngestTask --> VectorSvc
    IngestTask --> PG
    IngestTask --> WebhookSvc
    RefreshTask -->|enqueue ingest| Redis
    
    Chat --> WebhookSvc
    WebhookSvc --> ExtWebhooks
    
    Gateway --> PG
    Stats_R --> PG
```

## Multi-Tenancy Model

MiniRAG enforces strict data isolation at the application layer. Every database table includes a `tenant_id` column, and every query filters by the authenticated tenant.

- **SQL Layer**: All queries include `WHERE tenant_id = ?` from `AuthContext`. Cross-tenant FK references are validated at creation time.
- **Vector Store**: Qdrant uses a single collection `minirag_chunks` with tenant isolation via payload filters on every search and delete.
- **Worker Layer**: Background jobs receive `tenant_id` as an explicit parameter and scope all queries accordingly.

## Request Authentication Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant GW as API Gateway
    participant Auth as Auth Resolver
    participant DB as PostgreSQL
    
    C->>GW: Request + Bearer Token
    GW->>Auth: get_auth_context()
    
    alt Token contains dots (JWT)
        Auth->>Auth: decode_jwt(token)
        Auth-->>GW: AuthContext(tenant_id, user_id, role)
    else Opaque token (API key)
        Auth->>Auth: SHA-256 hash token
        Auth->>DB: SELECT FROM api_tokens WHERE hash = ?
        DB-->>Auth: ApiToken record
        Auth->>DB: UPDATE last_used_at
        Auth-->>GW: AuthContext(tenant_id, user_id, role)
    end
    
    GW->>GW: Route handler (tenant_id scoped)
```

## Security Layers

MiniRAG implements defense-in-depth with four cryptographic layers:

| Layer | Algorithm | Purpose |
|---|---|---|
| **Passwords** | Argon2id | Memory-hard hashing, resistant to GPU/ASIC attacks |
| **API Tokens** | SHA-256 | Deterministic lookup on every request; 256-bit entropy makes brute-force infeasible |
| **Field Encryption** | Fernet (AES-128-CBC) | Encrypt LLM provider API keys at rest in `BotProfile.encrypted_credentials` |
| **Sessions** | JWT (HS256) | Stateless session tokens with `sub`, `tid`, `role`, `exp` claims |

## RAG Pipeline (Chat Request)

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Chat Endpoint
    participant O as Orchestrator
    participant E as Embedding Service
    participant V as Qdrant
    participant LLM as LiteLLM
    participant DB as PostgreSQL
    participant WH as Webhook Dispatch
    
    C->>API: POST /v1/chat {message, bot_profile_id, stream?}
    API->>DB: Load bot profile + chat history
    API->>DB: Save user message
    
    API->>O: run_chat_turn() or run_chat_turn_stream()
    O->>E: embed(user_message)
    E->>LLM: aembedding()
    LLM-->>E: vector
    
    O->>V: search(vector, tenant_id, bot_profile_id, top_k=5)
    V-->>O: relevant chunks + scores
    
    O->>O: Build messages array<br/>[system_prompt + context + last 10 turns]
    
    alt stream=false
        O->>LLM: acompletion(messages)
        LLM-->>O: ChatResponse
        O-->>API: ChatResponse
        API->>DB: Save assistant message + usage event
        API->>WH: dispatch("chat.message")
        API-->>C: JSON response
    else stream=true
        O->>LLM: acompletion(messages, stream=True)
        loop For each token
            LLM-->>O: delta
            O-->>API: StreamEvent(delta)
            API-->>C: SSE: event: delta
        end
        API->>DB: Save assistant message + usage event
        API-->>C: SSE: event: done
    end
```

### Context Injection

The orchestrator injects retrieved chunks into the system prompt:

```
{system_prompt}

---
Relevant context from the knowledge base:
[1] {chunk_1_content}
[2] {chunk_2_content}
[3] {chunk_3_content}
---

Use the context above to answer the user's question.
```

## Streaming Protocol (SSE)

When `stream=true`, the response uses `Content-Type: text/event-stream` with three event types:

| Order | Event | Description |
|---|---|---|
| 1 | `sources` | Retrieved chunks with scores (sent before LLM starts) |
| 2 | `delta` | Token chunks as they arrive (repeated) |
| 3 | `done` | Final event with `chat_id`, `message_id`, and usage stats |

```
event: sources
data: {"sources": [{"chunk_id": "...", "content": "...", "score": 0.87}]}

event: delta
data: {"content": "Hello"}

event: delta
data: {"content": ", how can I help?"}

event: done
data: {"chat_id": "...", "message_id": "...", "usage": {"model": "gpt-4o-mini", "prompt_tokens": 1250, "completion_tokens": 340}}
```

Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (prevents proxy buffering).

Streaming metrics tracked in `UsageEvent`: `time_to_first_token_ms`, `stream_duration_ms`.

## Ingestion Pipeline

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Sources Endpoint
    participant R as Redis/ARQ
    participant W as Ingest Worker
    participant DB as PostgreSQL
    participant Q as Qdrant
    participant WH as Webhook Dispatch
    
    C->>API: POST /v1/sources/{id}/ingest
    API->>R: enqueue(ingest_source, source_id, tenant_id)
    API-->>C: 202 Accepted
    
    R->>W: ingest_source(source_id, tenant_id)
    W->>DB: UPDATE source SET status='processing'
    
    alt source_type = 'url'
        W->>W: httpx.get(url) then html_to_text()
    else source_type = 'text' or 'upload'
        W->>W: use source.content
    end
    
    W->>DB: INSERT document
    W->>W: chunk_text(content, size=512, overlap=64)
    W->>W: embed_texts(chunks) via LiteLLM
    W->>Q: delete old vectors for source
    W->>DB: INSERT chunks
    W->>Q: upsert vectors + payloads
    W->>DB: UPDATE source SET status='ready', last_refreshed_at=now()
    W->>WH: dispatch("source.ingested")
    
    Note over WH: On failure:
    W->>DB: UPDATE source SET status='error'
    W->>WH: dispatch("source.failed")
```

### Ingestion Steps

1. **Mark processing** — Update source status, clear error
2. **Extract content** — URL: HTTP GET + HTML-to-text; Text/Upload: use stored content
3. **Create document** — Store raw content with character count
4. **Chunk** — Recursive splitting (512 chars, 64 overlap) at semantic boundaries
5. **Embed** — Batch via LiteLLM `aembedding` (max 128 per batch)
6. **Cleanup** — Delete old Qdrant vectors for re-ingestion
7. **Persist** — Insert chunks to PostgreSQL, upsert vectors to Qdrant
8. **Finalize** — Update counters, set status to `ready`, set `last_refreshed_at`
9. **Notify** — Fire `source.ingested` webhook (or `source.failed` on error)

## Webhook Dispatch

```mermaid
sequenceDiagram
    participant Trigger as Event Source
    participant WH as Webhook Dispatch
    participant DB as PostgreSQL
    participant Ext as External URL
    
    Trigger->>WH: dispatch_webhook_event(tenant_id, event_type, payload)
    WH->>DB: SELECT webhooks WHERE tenant_id=? AND is_active=true
    DB-->>WH: matching webhooks
    
    loop For each webhook where event_type in events
        WH->>WH: body = JSON(payload)
        WH->>WH: signature = HMAC-SHA256(secret, body)
        WH->>Ext: POST url with signed payload
    end
```

### Supported Events

| Event Type | Source | Payload |
|---|---|---|
| `source.ingested` | Ingest worker | `source_id`, `source_name`, `document_count`, `chunk_count` |
| `source.failed` | Ingest worker | `source_id`, `error` |
| `chat.message` | Chat endpoint | `chat_id`, `message_id`, `bot_profile_id` |

### HMAC Verification

Every delivery includes `X-MiniRAG-Signature` (HMAC-SHA256 hex digest) and `X-MiniRAG-Event` headers.

**Verify in your endpoint:**

```python
import hmac, hashlib

def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Delivery timeout: 10s. Failures are logged but never block the triggering operation.

## Auto-Refresh Scheduling

```mermaid
graph LR
    subgraph "ARQ Cron (every 15 min)"
        Cron[check_refresh_schedules]
    end
    
    Cron -->|Query| DB[(PostgreSQL)]
    DB -->|Eligible sources| Cron
    Cron -->|Enqueue| Redis[(Redis)]
    Redis -->|ingest_source| Worker[Worker]
```

**Schedules:** `hourly` (1h), `daily` (24h), `weekly` (7d), `none` (disabled).

**Eligibility:** Source must have `refresh_schedule != none`, `status != processing`, `is_active = true`, and `last_refreshed_at + interval < now()`.

## Caching Strategy

Stats endpoints use an in-memory TTL cache (`app/core/cache.py`):

- **TTL**: 30 seconds
- **Scope**: Process-local, tenant-scoped keys like `("stats", "overview", tenant_id)`
- **Why not Redis**: Avoids network round-trip for read-heavy analytics; 30s staleness is acceptable

## Data Model

```mermaid
erDiagram
    Tenant ||--o{ User : has
    Tenant ||--o{ ApiToken : has
    Tenant ||--o{ BotProfile : has
    Tenant ||--o{ Source : has
    Tenant ||--o{ Chat : has
    Tenant ||--o{ Webhook : has
    
    User ||--o{ ApiToken : creates
    User ||--o{ Chat : participates
    
    BotProfile ||--o{ Source : configures
    BotProfile ||--o{ Chat : powers
    
    Source ||--o{ Document : produces
    Source ||--o{ Source : "parent-child"
    Document ||--o{ Chunk : contains
    
    Chat ||--o{ Message : contains
    Chat ||--o{ UsageEvent : tracks
    
    Tenant {
        uuid id PK
        string name
        string slug UK
    }
    
    BotProfile {
        uuid id PK
        uuid tenant_id FK
        string model
        text system_prompt
        text encrypted_credentials
    }
    
    Source {
        uuid id PK
        uuid tenant_id FK
        uuid bot_profile_id FK
        string source_type
        string status
        string refresh_schedule
    }
    
    Webhook {
        uuid id PK
        uuid tenant_id FK
        string url
        string secret
        text events
    }
    
    Chat {
        uuid id PK
        uuid tenant_id FK
        uuid bot_profile_id FK
    }
    
    Message {
        uuid id PK
        uuid chat_id FK
        string role
        text content
        string feedback
    }
    
    UsageEvent {
        uuid id PK
        uuid chat_id FK
        string model
        int prompt_tokens
        int completion_tokens
        bool is_stream
    }
```

### Enums

| Enum | Values |
|---|---|
| `UserRole` | `owner`, `admin`, `member` |
| `SourceType` | `text`, `upload`, `url` |
| `SourceStatus` | `pending`, `processing`, `ready`, `error` |
| `RefreshSchedule` | `none`, `hourly`, `daily`, `weekly` |
| `MessageRole` | `system`, `user`, `assistant` |
| `WebhookEvent` | `source.ingested`, `source.failed`, `chat.message` |

### Timestamp Convention

All tables include `created_at` and `updated_at` via `TimestampMixin`. Timestamps use `TIMESTAMP WITHOUT TIME ZONE` in UTC. Always use `utcnow()` from `app/models/base.py` — never `datetime.now(timezone.utc)` directly, as it breaks asyncpg.
