# Chat Widget Integration Guide

MiniRAG includes an embeddable chat widget that you can add to any website with a single `<script>` tag.

## Quick Start

```html
<script src="https://mini-rag.de/dashboard/widget/minirag-widget.js"
        data-bot-id="YOUR_BOT_PROFILE_ID"
        data-api-url="https://mini-rag.de"
        data-api-token="YOUR_API_TOKEN"
        data-title="Support Bot">
</script>
```

This adds a floating chat bubble to the bottom-right of the page. Clicking it opens the chat window.

## Configuration

### Script Tag Attributes

| Attribute | Required | Description |
|---|---|---|
| `data-bot-id` | Yes | UUID of the bot profile to use |
| `data-api-url` | Yes | Base URL of your MiniRAG API |
| `data-api-token` | Yes | API token for authentication |
| `data-title` | No | Chat window title (default: "MiniRAG Chat") |

### Custom Element

You can also use the widget as a custom HTML element for more control:

```html
<!-- Load the script (without auto-insert attributes) -->
<script src="https://mini-rag.de/dashboard/widget/minirag-widget.js"></script>

<!-- Place the widget where you want -->
<minirag-widget
  bot-id="YOUR_BOT_PROFILE_ID"
  api-url="https://mini-rag.de"
  api-token="YOUR_API_TOKEN"
  title="Help Assistant">
</minirag-widget>
```

## Features

- **Shadow DOM** — Styles are fully isolated; won't conflict with your page
- **Floating bubble** — Bottom-right chat bubble that expands to a chat window
- **Typing indicator** — Animated dots while waiting for a response
- **Source citations** — Expandable section showing retrieved context chunks and relevance scores
- **Conversation persistence** — Chat ID is maintained across messages in the same session
- **Error handling** — Graceful error messages if the API is unreachable

## Security

### API Token Scoping

- Create a dedicated API token for each widget deployment
- Use the dashboard to create tokens with descriptive names (e.g., "website-widget-prod")
- Tokens can be revoked instantly from the dashboard if compromised
- The API token is visible in the page source — this is expected for client-side widgets

### CORS

MiniRAG includes CORS middleware. For production, configure `allow_origins` in `app/main.py` to restrict which domains can call your API.

### Rate Limiting

Consider adding rate limiting (e.g., via a reverse proxy) to prevent abuse of widget-exposed endpoints.

## Styling

The widget uses CSS custom properties that you can override:

```css
minirag-widget {
  --primary: #4f46e5;        /* Primary color */
  --primary-hover: #4338ca;  /* Primary hover */
  --bg: #ffffff;             /* Background */
  --bg-secondary: #f3f4f6;  /* Secondary background */
  --text: #111827;           /* Text color */
  --text-secondary: #6b7280; /* Secondary text */
  --border: #e5e7eb;         /* Border color */
  --radius: 12px;            /* Border radius */
}
```

## Dashboard Integration

In the MiniRAG dashboard, each bot profile card has an **Embed** button that opens a modal with ready-to-copy snippets for all three integration methods (Script Tag, Custom Element, and Styling). Click **Copy Snippet** to copy the active tab's code — the bot ID and API URL are pre-filled automatically.

## Troubleshooting

| Issue | Solution |
|---|---|
| Widget doesn't appear | Check browser console for errors. Verify `data-bot-id` and `data-api-token` are set |
| CORS errors | Ensure your domain is in the `allow_origins` list |
| "Unauthorized" errors | Check that your API token is valid and not revoked |
| "Bot profile not found" | Verify the `data-bot-id` UUID matches an active bot profile |
