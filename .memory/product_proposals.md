# MiniRAG Product Feature Proposals

**Author**: Product Evangelist
**Date**: 2026-02-11
**Status**: Proposal

---

## Executive Summary

After deep exploration of the MiniRAG codebase and analysis of the competitive landscape (Ragie, Vectara, Nuclia, LangChain/LlamaIndex ecosystems, Pinecone Canopy, AnythingLLM), I've identified 7 high-impact feature proposals. MiniRAG's strengths are its clean multi-tenant architecture, provider-agnostic LLM layer (LiteLLM), beautiful no-build dashboard, and dead-simple embeddable widget. The proposals below are designed to make MiniRAG a must-have tool by doubling down on developer experience and self-service capabilities that competitors either gate behind enterprise pricing or ignore entirely.

---

## Feature Proposals (Ranked by Impact-to-Effort Ratio)

### 1. Streaming Chat Responses (SSE)

**Description**: Add Server-Sent Events (SSE) streaming to the `/v1/chat` endpoint and the embeddable widget so users see tokens appear in real-time instead of waiting for the full response.

**User Value**: This is the single biggest UX improvement possible. Currently, users stare at a spinner for 3-15 seconds while the LLM generates. Streaming makes the bot feel alive and responsive, reduces perceived latency dramatically, and matches what users expect from ChatGPT-era interfaces. For the widget embedded on customer websites, this is table stakes.

**Technical Approach**:
- Add `stream: bool = False` parameter to `ChatRequest`
- When `stream=True`, return `StreamingResponse` with `text/event-stream` content type
- Use `litellm.acompletion(..., stream=True)` which already returns an async iterator
- Emit SSE events: `data: {"delta": "token text", "type": "content"}` for each chunk
- Final event includes `usage`, `sources`, `chat_id`, and `message_id`
- Save the complete message and usage event after stream completes
- Update widget JS to use `EventSource` or `fetch` with `ReadableStream` reader
- Dashboard "Try It" panel also gets streaming rendering

**Estimated Complexity**: **M** (Medium)
The hard parts (LiteLLM streaming, async generators) are well-documented. Main work is the SSE response format, accumulating the full response for DB persistence, and updating the widget/dashboard JS.

---

### 2. Scheduled Source Re-Ingestion (Auto-Refresh)

**Description**: Allow sources (especially URL-type) to be re-ingested on a configurable schedule (hourly, daily, weekly). The system fetches fresh content, re-chunks, re-embeds, and replaces old vectors automatically.

**User Value**: Knowledge bases go stale. A customer's FAQ page changes, product docs get updated, pricing shifts. Today, users must manually trigger re-ingestion. Auto-refresh makes MiniRAG a "set and forget" knowledge pipeline -- this is what Ragie charges premium for and what most open-source RAG tools completely lack. It transforms MiniRAG from a static Q&A tool into a living knowledge system.

**Technical Approach**:
- Add `refresh_schedule` field to Source model (nullable, values: `hourly`, `daily`, `weekly`, `none`)
- Add `last_refreshed_at` timestamp to Source
- New ARQ periodic task `check_refresh_sources` that runs every 15 minutes
- Query sources where `refresh_schedule IS NOT NULL` and `last_refreshed_at + interval < now()`
- Enqueue `ingest_source` jobs for eligible sources
- For URL sources: implement actual HTTP fetch in `_extract_content()` (currently returns `source.content`)
- Dashboard: add schedule picker in source creation/edit form
- Sources page shows "Next refresh" indicator

**Estimated Complexity**: **M** (Medium)
The ARQ periodic task infrastructure already exists. The main new work is the URL fetcher (could use `httpx` + basic HTML-to-text via `beautifulsoup4` or `trafilatura`) and the scheduling logic.

---

### 3. Webhooks for Key Events

**Description**: Allow tenants to register webhook URLs that receive POST notifications when key events occur: source ingestion complete, ingestion failed, new chat message received, cost threshold exceeded.

**User Value**: Webhooks are the glue of modern SaaS integrations. They enable customers to build automated workflows: post a Slack message when ingestion completes, trigger a CI pipeline to update staging bots, alert an ops channel when errors spike, or sync chat transcripts to their CRM. Without webhooks, MiniRAG is an island. With them, it becomes connectable infrastructure.

**Technical Approach**:
- New `Webhook` model: `id`, `tenant_id`, `url`, `secret` (for HMAC signing), `events[]` (array of event types), `is_active`
- Event types: `source.ingested`, `source.failed`, `chat.message`, `usage.threshold`
- New API routes: `POST /v1/webhooks`, `GET /v1/webhooks`, `DELETE /v1/webhooks/{id}`
- Service layer: `dispatch_webhook(tenant_id, event_type, payload)` -- uses `httpx.AsyncClient` with retry (3x exponential backoff)
- Hook into existing code: after `ingest_source` completes/fails in `workers/ingest.py`, after chat response in `api/v1/chat.py`
- Include HMAC-SHA256 signature in `X-MiniRAG-Signature` header using the webhook secret
- Dashboard: Webhooks management page (CRUD + test ping button)

**Estimated Complexity**: **M** (Medium)
Clean separation of concerns. The webhook dispatch is a fire-and-forget async task. HMAC signing is straightforward. The model + CRUD follows established patterns.

---

### 4. Conversation Export & Analytics API

**Description**: Add endpoints to export chat transcripts (JSON, CSV) and a feedback analytics endpoint that aggregates thumbs-up/down signals across conversations, sources, and time periods.

**User Value**: Businesses embedding the widget need to know: "Is our bot actually helping?" The feedback mechanism already exists (positive/negative on messages) but there's no way to aggregate or export this data. Export enables: training data curation, compliance archival, quality auditing, and feeding insights back into system prompt refinement. The analytics endpoint lets product teams build dashboards showing answer quality trends over time.

**Technical Approach**:
- `GET /v1/chat/{id}/export?format=json|csv` -- returns full transcript with metadata
- `GET /v1/chat/export?bot_profile_id=...&from=...&to=...&format=json|csv` -- bulk export with date range filtering
- `GET /v1/stats/feedback` -- aggregated feedback stats:
  - Total positive/negative/neutral by bot_profile
  - Feedback rate (% of messages that received feedback)
  - Trend over time (daily/weekly)
  - Worst-performing sources (messages with negative feedback -> context_chunks -> source_id)
- CSV export uses Python's `csv` module with `StreamingResponse` for large datasets
- Dashboard: "Feedback" tab on Usage page with charts; export buttons on Chat History page

**Estimated Complexity**: **S** (Small)
Most data structures already exist. The `Message.feedback` field and `context_chunks` JSON are already populated. This is primarily query-writing and serialization work.

---

### 5. Prompt Playground (A/B Testing for System Prompts)

**Description**: Add a built-in prompt testing tool that lets users compare different system prompts side-by-side against the same set of test questions, with quality scores derived from retrieval relevance and user-defined evaluation criteria.

**User Value**: System prompt engineering is the #1 lever for RAG quality, yet most platforms offer zero tooling for it. Users currently edit the prompt, chat with the bot, evaluate subjectively, and repeat. A structured playground with saved test suites and side-by-side comparison makes prompt optimization systematic rather than guesswork. This is genuinely differentiated -- even LangSmith/LangFuse focus on observability, not on making prompt iteration delightful for non-engineers.

**Technical Approach**:
- New models: `PromptTest` (id, tenant_id, bot_profile_id, name, test_questions JSON array)
- New model: `PromptTestRun` (id, test_id, system_prompt_used, results JSON, avg_relevance_score, created_at)
- `POST /v1/bot-profiles/{id}/playground/run` -- accepts `system_prompt` override + `test_questions[]`
- Runs each question through the RAG pipeline (embed -> search -> LLM) with the override prompt
- Returns: answers, retrieved chunks with scores, token usage
- Dashboard: split-pane UI showing Run A vs Run B results side by side
- Optional: auto-evaluate using a lightweight LLM call ("Rate this answer 1-5 for helpfulness given the context")

**Estimated Complexity**: **L** (Large)
This requires new models, a non-trivial API, and significant dashboard UI work (split-pane comparison). However, it's extremely high-value as a differentiator.

---

### 6. Multi-Language Source Ingestion & Query Routing

**Description**: Detect the language of ingested content and user queries, then use language-aware embedding models or cross-lingual retrieval to serve multilingual knowledge bases from a single bot profile.

**User Value**: MiniRAG is deployed at mini-rag.de -- the German market is a primary audience. Many businesses operate in multiple languages (German site + English docs, or multilingual support centers). Today, if you ingest German content and ask an English question, retrieval quality degrades because `text-embedding-3-small` works best within a single language. Language-aware RAG is a compelling enterprise feature that competitors like Vectara highlight as a key differentiator.

**Technical Approach**:
- Add `language` field to Source model (auto-detected or user-specified)
- Use a lightweight language detection library (`langdetect` or `lingua-py`) during ingestion
- Store detected language in Qdrant payload alongside chunks
- At query time: detect query language, optionally translate query using LLM before embedding
- Alternative approach: use multilingual embedding models (e.g., `text-embedding-3-large` has better cross-lingual performance, or Cohere's `embed-multilingual-v3.0` via LiteLLM)
- Add `embedding_model` field to BotProfile so tenants can choose multilingual models
- Qdrant search can filter/boost by language match

**Estimated Complexity**: **L** (Large)
The embedding model switch is straightforward via LiteLLM, but handling mixed-language collections, query translation, and maintaining separate vector dimensions per model adds significant complexity.

---

### 7. Source Connectors: Web Crawler & Sitemap Importer

**Description**: Add a web crawler connector that, given a root URL and optional sitemap URL, automatically discovers pages, extracts content, and creates child sources. Supports respecting robots.txt and crawl depth limits.

**User Value**: The most common use case for RAG is "make my website searchable via chat." Today, users must manually paste URLs or upload files one by one. A crawler that ingests an entire site from a single URL dramatically reduces time-to-value. Combined with scheduled re-ingestion (#2), this creates a fully automated "website to chatbot" pipeline -- the exact product that Ragie, ChatBase, and CustomGPT charge $50-500/month for.

**Technical Approach**:
- New source type: `SourceType.SITEMAP` or extend `URL` type with crawl config
- Config schema: `{"root_url": "...", "sitemap_url": "...", "max_pages": 50, "max_depth": 3, "respect_robots": true}`
- New worker task `crawl_source`:
  1. Fetch sitemap.xml (or discover links from root page)
  2. Create child Source records for each discovered URL
  3. For each child: fetch page, extract text (using `trafilatura` or `beautifulsoup4`), set as content
  4. Trigger batch ingestion of all children
- Rate limiting: 1 req/second with configurable delay
- Dashboard: "Import Website" flow with URL input, preview of discovered pages, and one-click ingest
- Use parent/child source hierarchy (already exists!) -- parent is the site, children are individual pages

**Estimated Complexity**: **L** (Large)
Web crawling is inherently messy (JS-rendered pages, auth walls, rate limits, encoding issues). However, starting with sitemap-based discovery + static page extraction covers 80% of use cases with manageable complexity. The parent/child source model is already built.

---

## Priority Matrix

| # | Feature | Impact | Effort | Priority Score |
|---|---------|--------|--------|---------------|
| 1 | Streaming Chat (SSE) | Very High | Medium | **1st** |
| 4 | Conversation Export & Feedback Analytics | High | Small | **2nd** |
| 3 | Webhooks | High | Medium | **3rd** |
| 2 | Scheduled Re-Ingestion | Very High | Medium | **4th** |
| 7 | Web Crawler / Sitemap Import | Very High | Large | **5th** |
| 5 | Prompt Playground | High | Large | **6th** |
| 6 | Multi-Language Support | Medium | Large | **7th** |

## Rationale

**Streaming (#1)** is the clear #1 priority because it's the most visible UX improvement with the broadest impact -- every single chat interaction benefits from it, and it transforms the widget from "functional" to "polished."

**Export & Analytics (#4)** ranks second because it's low effort with high payoff -- the data already exists, and it unlocks an entire category of use cases (quality monitoring, compliance, data curation) that make MiniRAG sticky for business users.

**Webhooks (#3)** are the gateway to ecosystem integration. They're medium effort but unlock exponential value as they make MiniRAG composable with any workflow tool.

**Scheduled Re-Ingestion (#2)** and **Web Crawler (#7)** together create the "website to chatbot in 60 seconds" story that makes MiniRAG immediately compelling for the largest addressable market (small businesses wanting a website FAQ bot).

**Prompt Playground (#5)** is the most differentiated feature on this list -- nobody does this well -- but it requires the most dashboard engineering and is higher effort relative to its audience size.

**Multi-Language (#6)** is important for the German market but can be partially addressed through model selection (using multilingual embeddings) without major platform changes.
