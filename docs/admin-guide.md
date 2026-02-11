# Admin Dashboard Guide

## Accessing the Dashboard

Navigate to `http://localhost:8000/dashboard` (or your deployed URL).

## Login

Sign in with the email and password you used when bootstrapping your tenant. The dashboard uses JWT authentication — your session lasts for the configured `JWT_EXPIRE_MINUTES` (default: 60 minutes).

## Pages

### Overview

The landing page shows:
- **Service health** — connectivity status for PostgreSQL, Qdrant, and Redis
- **Summary stats** — count of bot profiles, sources, chats, and total tokens used
- **Quick actions** — shortcuts to create bots, sources, or API tokens

### Bot Profiles

Manage your AI assistant configurations:

1. **Create** — Click "+ New Bot", fill in name, model, system prompt, and optional API key
2. **Edit** — Click "Edit" on any card to modify settings
3. **Try It** — Click "Try It" to open an inline chat with real-time streaming and test the bot immediately
4. **Embed** — Get ready-to-copy snippet for embedding the widget on any website
5. **Delete** — Soft-deletes (deactivates) the profile

**Fields:**
- **Name** — Display name for the bot
- **Model** — LLM model identifier (e.g., `gpt-4o-mini`, `claude-sonnet-4-5`)
- **System Prompt** — Instructions that define the bot's personality and behavior
- **Temperature** — Controls randomness (0 = deterministic, 2 = creative)
- **Max Tokens** — Maximum response length
- **API Key** — Provider API key, encrypted at rest with Fernet

### Sources

Knowledge sources that power your bot's RAG responses:

1. **Create (Text)** — Select a bot profile, name your source, and paste text content
2. **Create (URL)** — Enter a URL; the system fetches the page and extracts text automatically
3. **Upload File** — Upload `.txt`, `.md`, `.csv`, `.pdf`, or `.docx` files for automatic text extraction
4. **Batch Create** — Create multiple child sources under a parent (e.g., from a sitemap)
5. **Ingest** — Click "Ingest" to process the source into chunks and vectors. Status: `pending` → `processing` → `ready`
6. **Delete** — Soft-deletes the source and its vectors

**Auto-Refresh (URL sources):**
Set a refresh schedule when creating or editing a URL source:
- **Hourly** — Re-ingest every hour
- **Daily** — Re-ingest every 24 hours
- **Weekly** — Re-ingest every 7 days
- **None** — Manual ingestion only (default)

The worker checks for eligible sources every 15 minutes and automatically re-fetches, re-chunks, and re-embeds content.

**Source Hierarchy:**
Sources support a parent/child structure. A parent source (e.g., "Company Website") can contain multiple child sources (individual pages). Batch creation creates children under a parent automatically.

### Chat History

Browse all chat sessions:
- View conversation title, message count, and token usage
- Click "View" to see the full message thread with streaming responses
- **Feedback** — Click thumbs up/down on assistant messages to rate quality
- **Export** — Download conversations as JSON or CSV for analysis or compliance

### Webhooks

Configure HTTP notifications for key platform events:

1. **Create** — Set a URL, select events to subscribe to, and optionally provide a signing secret (auto-generated if omitted). The raw secret is shown once at creation — save it immediately.
2. **Test** — Click "Test Ping" to send a verification request and confirm connectivity
3. **Delete** — Remove a webhook permanently

**Event Types:**

| Event | Trigger |
|---|---|
| `source.ingested` | Source successfully processed into chunks and vectors |
| `source.failed` | Source ingestion failed with an error |
| `chat.message` | New assistant response generated |

**Security:**
Every webhook delivery includes:
- `X-MiniRAG-Signature` header — HMAC-SHA256 signature of the request body using the webhook's secret
- `X-MiniRAG-Event` header — The event type string

Verify the signature in your receiving endpoint to ensure authenticity:

```python
import hmac, hashlib

def verify_signature(body: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### API Tokens

Manage API tokens for programmatic access:

1. **Create** — Name your token. The raw token is shown **once** — copy it immediately
2. **Revoke** — Permanently disable a token

### Users

Manage team members (owner/admin only):

1. **Create** — Add users with email, password, and role
2. **Deactivate** — Disable a user's access

**Roles:**
- **Owner** — Full access, can manage users and all resources
- **Admin** — Can manage users and resources
- **Member** — Can use bots and view resources

### Usage & Analytics

Token consumption analytics with cost tracking:

- **Daily chart** — Bar chart of total tokens per day (filterable by date range)
- **Model breakdown** — Doughnut chart showing usage by model
- **Cost estimates** — Total cost, daily average, and projected monthly spend
- **Per-bot breakdown** — Usage and cost by bot profile
- **Model pricing** — Pricing table fetched from the API (automatically updated)
- **Loading skeletons** — Animated placeholders while data loads

**Feedback Analytics:**
- **Overview** — Total positive/negative feedback counts with feedback rate percentage
- **Per-bot breakdown** — See which bots get the best/worst feedback
- **Trend chart** — Daily feedback trend over time to spot quality changes

### Settings

System information:
- **Tenant info** — Name, slug, plan, status
- **System health** — Real-time connectivity checks for all backing services
- **App info** — Version and link to API docs
