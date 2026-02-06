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
3. **Try It** — Click "Try It" to open an inline chat and test the bot immediately
4. **Delete** — Soft-deletes (deactivates) the profile

**Fields:**
- **Name** — Display name for the bot
- **Model** — LLM model identifier (e.g., `gpt-4o-mini`, `claude-sonnet-4-5`)
- **System Prompt** — Instructions that define the bot's personality and behavior
- **Temperature** — Controls randomness (0 = deterministic, 2 = creative)
- **Max Tokens** — Maximum response length
- **API Key** — Provider API key, encrypted at rest with Fernet

### Sources

Knowledge sources that power your bot's RAG responses:

1. **Create** — Select a bot profile, name your source, choose type (text/URL), and add content
2. **Ingest** — Click "Ingest" to process the source into chunks and vectors. Status will change from `pending` → `processing` → `ready`
3. **Delete** — Soft-deletes the source

### Chat History

Browse all chat sessions:
- View conversation title, message count, and token usage
- Click "View" to see the full message thread

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

Token consumption analytics:
- **Daily chart** — Bar chart of total tokens per day
- **Model breakdown** — Doughnut chart showing usage by model
- **Detail table** — Per-day, per-model breakdown with request counts

### Settings

System information:
- **Tenant info** — Name, slug, plan, status
- **System health** — Real-time connectivity checks for all backing services
- **App info** — Version and link to API docs
