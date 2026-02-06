/**
 * MiniRAG Dashboard — Alpine.js application.
 */
document.addEventListener('alpine:init', () => {

  // ── Global auth store ───────────────────────────────────────
  Alpine.store('auth', {
    user: null,
    tenant: null,
    isLoggedIn: false,

    async init() {
      if (API.getToken()) {
        try {
          const data = await API.me();
          this.user = data.user;
          this.tenant = data.tenant;
          this.isLoggedIn = true;
        } catch {
          API.clearToken();
        }
      }
    },

    async login(email, password) {
      const data = await API.login(email, password);
      API.setToken(data.access_token);
      this.user = data.user;
      this.tenant = data.tenant;
      this.isLoggedIn = true;
    },

    logout() {
      API.clearToken();
      this.user = null;
      this.tenant = null;
      this.isLoggedIn = false;
    },

    get isOwner() { return this.user?.role === 'owner'; },
    get isAdmin() { return this.user?.role === 'admin' || this.user?.role === 'owner'; },
  });

  // ── Toast notification store ────────────────────────────────
  Alpine.store('toast', {
    message: '',
    type: 'info',
    visible: false,
    _timeout: null,

    show(message, type = 'info') {
      this.message = message;
      this.type = type;
      this.visible = true;
      clearTimeout(this._timeout);
      this._timeout = setTimeout(() => { this.visible = false; }, 4000);
    },

    success(msg) { this.show(msg, 'success'); },
    error(msg) { this.show(msg, 'error'); },
    info(msg) { this.show(msg, 'info'); },
  });
});

// ── Redirect on 401 ────────────────────────────────────────
window.addEventListener('minirag:unauthorized', () => {
  Alpine.store('auth')?.logout();
});

// ── Helpers ─────────────────────────────────────────────────
function formatDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function truncate(s, n = 60) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '...' : s;
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(
    () => Alpine.store('toast').success('Copied to clipboard'),
    () => Alpine.store('toast').error('Failed to copy'),
  );
}
