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
  getUsage() {
    return this.get('/v1/stats/usage');
  },

  // ── System ────────────────────────────────────────────
  getSystemHealth() {
    return this.get('/v1/system/health');
  },
};
