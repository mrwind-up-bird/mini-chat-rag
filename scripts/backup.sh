#!/usr/bin/env bash
set -euo pipefail

# ── MiniRAG Backup Script ───────────────────────────────────
# Backs up PostgreSQL and Qdrant. Add to cron:
#   0 3 * * * /opt/minirag/scripts/backup.sh >> /opt/minirag/backups/backup.log 2>&1

BACKUP_DIR="${BACKUP_DIR:-/opt/minirag/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPOSE_DIR="${COMPOSE_DIR:-/opt/minirag}"

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup..."

# ── PostgreSQL dump ─────────────────────────────────────────
echo "[$(date)] Dumping PostgreSQL..."
docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-minirag}" "${POSTGRES_DB:-minirag}" \
    | gzip > "$BACKUP_DIR/postgres_${TIMESTAMP}.sql.gz"
echo "[$(date)] PostgreSQL dump: postgres_${TIMESTAMP}.sql.gz"

# ── Qdrant snapshot ─────────────────────────────────────────
echo "[$(date)] Creating Qdrant snapshot..."
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
SNAPSHOT_RESP=$(curl -sf -X POST "$QDRANT_URL/collections/minirag_chunks/snapshots" 2>/dev/null || echo "")
if [ -n "$SNAPSHOT_RESP" ]; then
    SNAPSHOT_NAME=$(echo "$SNAPSHOT_RESP" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)
    if [ -n "$SNAPSHOT_NAME" ]; then
        curl -sf "$QDRANT_URL/collections/minirag_chunks/snapshots/$SNAPSHOT_NAME" \
            -o "$BACKUP_DIR/qdrant_${TIMESTAMP}.snapshot"
        echo "[$(date)] Qdrant snapshot: qdrant_${TIMESTAMP}.snapshot"
    else
        echo "[$(date)] WARNING: Could not parse Qdrant snapshot name"
    fi
else
    echo "[$(date)] WARNING: Qdrant snapshot failed (collection may not exist yet)"
fi

# ── Cleanup old backups ─────────────────────────────────────
echo "[$(date)] Cleaning up backups older than ${RETAIN_DAYS} days..."
find "$BACKUP_DIR" -name "postgres_*.sql.gz" -mtime +"$RETAIN_DAYS" -delete
find "$BACKUP_DIR" -name "qdrant_*.snapshot" -mtime +"$RETAIN_DAYS" -delete

echo "[$(date)] Backup complete."
