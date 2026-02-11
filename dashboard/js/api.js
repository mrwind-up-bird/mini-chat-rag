/**
 * MiniRAG API client — wraps fetch with auth header injection and error handling.
 */
const API = {
  baseUrl: window.location.origin,

  getToken() {
    return localStorage.getItem('minirag_token');
  },

  setToken(token) {
    localStorage.setItem('minirag_token', token);
  },

  clearToken() {
    localStorage.removeItem('minirag_token');
  },

  async request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const opts = { method, headers };
    if (body && method !== 'GET') {
      opts.body = JSON.stringify(body);
    }

    const resp = await fetch(`${this.baseUrl}${path}`, opts);

    if (resp.status === 401) {
      this.clearToken();
      window.dispatchEvent(new CustomEvent('minirag:unauthorized'));
      throw new Error('Unauthorized');
    }

    if (resp.status === 204) return null;

    const data = await resp.json();

    if (!resp.ok) {
      const msg = data.detail || `HTTP ${resp.status}`;
      throw new Error(msg);
    }

    return data;
  },

  get(path) { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },
  patch(path, body) { return this.request('PATCH', path, body); },
  del(path) { return this.request('DELETE', path); },

  // ── Auth ──────────────────────────────────────────────
  login(email, password) {
    return this.post('/v1/auth/login', { email, password });
  },
  me() {
    return this.get('/v1/auth/me');
  },

  // ── Tenants ───────────────────────────────────────────
  getTenant() {
    return this.get('/v1/tenants/me');
  },

  // ── Bot Profiles ──────────────────────────────────────
  listBotProfiles() {
    return this.get('/v1/bot-profiles');
  },
  getBotProfile(id) {
    return this.get(`/v1/bot-profiles/${id}`);
  },
  createBotProfile(data) {
    return this.post('/v1/bot-profiles', data);
  },
  updateBotProfile(id, data) {
    return this.patch(`/v1/bot-profiles/${id}`, data);
  },
  deleteBotProfile(id) {
    return this.del(`/v1/bot-profiles/${id}`);
  },

  // ── Sources ───────────────────────────────────────────
  listSources() {
    return this.get('/v1/sources');
  },
  createSource(data) {
    return this.post('/v1/sources', data);
  },
  updateSource(id, data) {
    return this.patch(`/v1/sources/${id}`, data);
  },
  deleteSource(id) {
    return this.del(`/v1/sources/${id}`);
  },
  triggerIngest(id) {
    return this.post(`/v1/sources/${id}/ingest`);
  },
  listSourceChildren(parentId) {
    return this.get(`/v1/sources/${parentId}/children`);
  },
  createBatchSource(data) {
    return this.post('/v1/sources/batch', data);
  },
  triggerIngestChildren(parentId) {
    return this.post(`/v1/sources/${parentId}/ingest-children`);
  },
  async uploadSource(formData) {
    const headers = {};
    const token = this.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const resp = await fetch(`${this.baseUrl}/v1/sources/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (resp.status === 401) {
      this.clearToken();
      window.dispatchEvent(new CustomEvent('minirag:unauthorized'));
      throw new Error('Unauthorized');
    }
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    return data;
  },

  // ── Chat ──────────────────────────────────────────────
  listChats(botProfileId = null, limit = 50, offset = 0) {
    let path = `/v1/chat?limit=${limit}&offset=${offset}`;
    if (botProfileId) path += `&bot_profile_id=${botProfileId}`;
    return this.get(path);
  },
  getChat(id) {
    return this.get(`/v1/chat/${id}`);
  },
  getChatMessages(id) {
    return this.get(`/v1/chat/${id}/messages`);
  },
  sendMessage(botProfileId, message, chatId = null) {
    const body = { bot_profile_id: botProfileId, message };
    if (chatId) body.chat_id = chatId;
    return this.post('/v1/chat', body);
  },

  /**
   * Send a chat message with streaming SSE response.
   *
   * @param {string} botProfileId - Bot profile UUID
   * @param {string} message - User message text
   * @param {string|null} chatId - Existing chat session ID (null for new)
   * @param {object} callbacks - { onDelta, onSources, onDone, onError }
   * @returns {Promise<void>}
   */
  async sendMessageStream(botProfileId, message, chatId, { onDelta, onSources, onDone, onError }) {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const body = { bot_profile_id: botProfileId, message, stream: true };
    if (chatId) body.chat_id = chatId;

    let resp;
    try {
      resp = await fetch(`${this.baseUrl}/v1/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });
    } catch (err) {
      onError?.(err.message || 'Network error');
      return;
    }

    if (resp.status === 401) {
      this.clearToken();
      window.dispatchEvent(new CustomEvent('minirag:unauthorized'));
      onError?.('Unauthorized');
      return;
    }

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      onError?.(data.detail || `HTTP ${resp.status}`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // Keep incomplete part in buffer

        for (const part of parts) {
          if (!part.trim()) continue;

          let eventType = 'message';
          let dataStr = '';

          for (const line of part.split('\n')) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7);
            } else if (line.startsWith('data: ')) {
              dataStr += line.slice(6);
            }
          }

          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);

            switch (eventType) {
              case 'delta':
                onDelta?.(data.content);
                break;
              case 'sources':
                onSources?.(data.sources);
                break;
              case 'done':
                onDone?.(data);
                break;
              case 'error':
                onError?.(data.detail || 'Stream error');
                break;
            }
          } catch {
            // Ignore malformed JSON
          }
        }
      }
    } catch (err) {
      onError?.(err.message || 'Stream read error');
    }
  },
  submitFeedback(chatId, messageId, feedback) {
    return this.patch(`/v1/chat/${chatId}/messages/${messageId}/feedback`, { feedback });
  },

  // ── API Tokens ────────────────────────────────────────
  listApiTokens() {
    return this.get('/v1/api-tokens');
  },
  createApiToken(data) {
    return this.post('/v1/api-tokens', data);
  },
  revokeApiToken(id) {
    return this.del(`/v1/api-tokens/${id}`);
  },

  // ── Users ─────────────────────────────────────────────
  listUsers() {
    return this.get('/v1/users');
  },
  createUser(data) {
    return this.post('/v1/users', data);
  },
  updateUser(id, data) {
    return this.patch(`/v1/users/${id}`, data);
  },
  deleteUser(id) {
    return this.del(`/v1/users/${id}`);
  },

  // ── Stats ─────────────────────────────────────────────
  getOverview() {
    return this.get('/v1/stats/overview');
  },
  getUsage(days = null) {
    return this.get('/v1/stats/usage' + (days ? `?days=${days}` : ''));
  },
  getUsageByBot(days = null) {
    return this.get('/v1/stats/usage/by-bot' + (days ? `?days=${days}` : ''));
  },
  getUsageByModel(days = null) {
    return this.get('/v1/stats/usage/by-model' + (days ? `?days=${days}` : ''));
  },
  getCostEstimate(days = 30) {
    return this.get(`/v1/stats/cost-estimate?days=${days}`);
  },
  getPricing() {
    return this.get('/v1/stats/pricing');
  },

  // ── System ────────────────────────────────────────────
  getSystemHealth() {
    return this.get('/v1/system/health');
  },
};
