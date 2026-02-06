/**
 * MiniRAG Embeddable Chat Widget
 *
 * Usage:
 *   <script src="https://your-host/dashboard/widget/minirag-widget.js"
 *           data-bot-id="uuid"
 *           data-api-url="https://your-host"
 *           data-api-token="your-api-token"
 *           data-title="Support Bot">
 *   </script>
 */
(function () {
  'use strict';

  // ── Read config from script tag ──────────────────────────
  const scriptEl = document.currentScript;
  const config = {
    botId: scriptEl?.getAttribute('data-bot-id') || '',
    apiUrl: scriptEl?.getAttribute('data-api-url') || window.location.origin,
    apiToken: scriptEl?.getAttribute('data-api-token') || '',
    title: scriptEl?.getAttribute('data-title') || 'MiniRAG Chat',
  };

  // ── Fetch CSS ────────────────────────────────────────────
  const cssUrl = new URL('minirag-widget.css', scriptEl?.src || window.location.href).href;

  class MiniRAGWidget extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._open = false;
      this._chatId = null;
      this._messages = [];
      this._sending = false;
    }

    connectedCallback() {
      // Allow attribute overrides on the custom element itself
      const botId = this.getAttribute('bot-id') || config.botId;
      const apiUrl = this.getAttribute('api-url') || config.apiUrl;
      const apiToken = this.getAttribute('api-token') || config.apiToken;
      const title = this.getAttribute('title') || config.title;

      this._config = { botId, apiUrl, apiToken, title };

      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = cssUrl;
      this.shadowRoot.appendChild(link);

      this._container = document.createElement('div');
      this.shadowRoot.appendChild(this._container);
      this._render();
    }

    _render() {
      const c = this._container;
      c.innerHTML = '';

      // Bubble button
      if (!this._open) {
        const bubble = document.createElement('button');
        bubble.className = 'widget-bubble';
        bubble.innerHTML = `<svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
        </svg>`;
        bubble.onclick = () => { this._open = true; this._render(); };
        c.appendChild(bubble);
        return;
      }

      // Chat window
      const win = document.createElement('div');
      win.className = 'widget-window';

      // Header
      const header = document.createElement('div');
      header.className = 'widget-header';
      header.innerHTML = `
        <span class="widget-header-title">${this._escHtml(this._config.title)}</span>
        <button class="widget-header-close">&times;</button>
      `;
      header.querySelector('.widget-header-close').onclick = () => {
        this._open = false;
        this._render();
      };
      win.appendChild(header);

      // Messages
      const msgs = document.createElement('div');
      msgs.className = 'widget-messages';

      if (this._messages.length === 0) {
        const welcome = document.createElement('div');
        welcome.className = 'msg msg-assistant';
        welcome.textContent = 'Hello! How can I help you today?';
        msgs.appendChild(welcome);
      }

      this._messages.forEach(m => {
        const div = document.createElement('div');
        div.className = `msg msg-${m.role}`;
        div.textContent = m.content;

        if (m.sources && m.sources.length > 0) {
          const details = document.createElement('details');
          details.className = 'msg-sources';
          const summary = document.createElement('summary');
          summary.textContent = `${m.sources.length} source(s) used`;
          details.appendChild(summary);
          m.sources.forEach(s => {
            const p = document.createElement('p');
            p.textContent = `[${(s.score * 100).toFixed(0)}%] ${s.content}`;
            details.appendChild(p);
          });
          div.appendChild(details);
        }

        msgs.appendChild(div);
      });

      if (this._sending) {
        const typing = document.createElement('div');
        typing.className = 'typing-indicator';
        typing.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
        msgs.appendChild(typing);
      }

      win.appendChild(msgs);

      // Input bar
      const bar = document.createElement('div');
      bar.className = 'widget-input-bar';
      const input = document.createElement('input');
      input.className = 'widget-input';
      input.placeholder = 'Type a message...';
      input.disabled = this._sending;

      const sendBtn = document.createElement('button');
      sendBtn.className = 'widget-send';
      sendBtn.disabled = this._sending;
      sendBtn.innerHTML = `<svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
      </svg>`;

      const send = () => {
        const text = input.value.trim();
        if (!text || this._sending) return;
        input.value = '';
        this._send(text);
      };

      input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
      sendBtn.onclick = send;

      bar.appendChild(input);
      bar.appendChild(sendBtn);
      win.appendChild(bar);

      // Powered by
      const powered = document.createElement('div');
      powered.className = 'widget-powered';
      powered.innerHTML = 'Powered by <a href="https://github.com" target="_blank">MiniRAG</a>';
      win.appendChild(powered);

      // Bubble (behind window)
      const bubble2 = document.createElement('button');
      bubble2.className = 'widget-bubble';
      bubble2.innerHTML = `<svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
      </svg>`;
      bubble2.onclick = () => { this._open = false; this._render(); };

      c.appendChild(win);
      c.appendChild(bubble2);

      // Scroll to bottom
      requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; });
      // Focus input
      requestAnimationFrame(() => input.focus());
    }

    async _send(text) {
      this._messages.push({ role: 'user', content: text });
      this._sending = true;
      this._render();

      try {
        const resp = await fetch(`${this._config.apiUrl}/v1/chat`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this._config.apiToken}`,
          },
          body: JSON.stringify({
            bot_profile_id: this._config.botId,
            message: text,
            chat_id: this._chatId || undefined,
          }),
        });

        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }

        const data = await resp.json();
        this._chatId = data.chat_id;
        this._messages.push({
          role: 'assistant',
          content: data.message.content,
          sources: data.sources || [],
        });
      } catch (e) {
        this._messages.push({ role: 'assistant', content: `Sorry, something went wrong: ${e.message}` });
      }

      this._sending = false;
      this._render();
    }

    _escHtml(s) {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }
  }

  // Register custom element
  if (!customElements.get('minirag-widget')) {
    customElements.define('minirag-widget', MiniRAGWidget);
  }

  // Auto-insert if loaded via script tag with config
  if (config.botId && config.apiToken) {
    const el = document.createElement('minirag-widget');
    document.body.appendChild(el);
  }
})();
