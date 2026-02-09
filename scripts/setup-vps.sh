#!/usr/bin/env bash
set -euo pipefail

# ── MiniRAG VPS Setup Script ────────────────────────────────
# Run once on a fresh Ubuntu 24.04 server (Hetzner CX22 or similar)
# Usage: curl -sSL <raw-url> | bash  OR  bash scripts/setup-vps.sh

echo "==> Installing Docker..."
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> Configuring firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw --force enable

echo "==> Creating minirag user and project directory..."
useradd -r -s /usr/sbin/nologin minirag || true
mkdir -p /opt/minirag/backups
chown -R minirag:minirag /opt/minirag

echo "==> Adding deploy user to docker group..."
# Assumes you SSH in as a non-root user; adjust as needed
if [ -n "${SUDO_USER:-}" ]; then
    usermod -aG docker "$SUDO_USER"
fi

echo "==> Generating secrets and creating .env..."
FERNET_KEY=$(docker run --rm python:3.11-slim python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "GENERATE_ME")
JWT_SECRET=$(openssl rand -hex 32)
PG_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/')

cat > /opt/minirag/.env <<EOF
# ── Domain ────────────────────────────────────────────────
DOMAIN=

# ── Database ──────────────────────────────────────────────
POSTGRES_USER=minirag
POSTGRES_PASSWORD=${PG_PASSWORD}
POSTGRES_DB=minirag
DATABASE_URL=postgresql+asyncpg://minirag:${PG_PASSWORD}@postgres:5432/minirag

# ── Redis ─────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Qdrant ────────────────────────────────────────────────
QDRANT_URL=http://qdrant:6333

# ── Security ──────────────────────────────────────────────
ENCRYPTION_KEY=${FERNET_KEY}
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# ── CORS ──────────────────────────────────────────────────
ALLOWED_ORIGINS=*

# ── LLM ───────────────────────────────────────────────────
DEFAULT_LLM_MODEL=gpt-4o-mini
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
EOF

chmod 600 /opt/minirag/.env
chown minirag:minirag /opt/minirag/.env

echo ""
echo "============================================"
echo "  MiniRAG VPS setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Point your domain A record to this server's IP"
echo "  2. Edit /opt/minirag/.env:"
echo "     - Set DOMAIN to your domain name"
echo "     - Set ALLOWED_ORIGINS to https://yourdomain.com"
echo "     - Add LLM API keys"
echo "  3. Copy docker-compose.yml, docker-compose.prod.yml,"
echo "     and Caddyfile to /opt/minirag/"
echo "  4. cd /opt/minirag && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
echo "  5. Verify: curl https://yourdomain.com/health"
echo ""
