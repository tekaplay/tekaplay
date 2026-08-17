#!/usr/bin/env bash
#
# Take a verified, portable backup of the Tekaplay database.
#
#   ./scripts/backup.sh                      # uses $DATABASE_URL
#   ./scripts/backup.sh "postgresql://..."   # or an explicit URL
#
# Why this exists rather than "the provider has backups": a provider's
# automated backups live inside that provider and disappear with the account.
# This produces a pg_dump custom-format archive on your own disk that restores
# into ANY PostgreSQL 16 server — Neon, RDS, Azure, or a laptop. That is the
# thing that makes leaving a provider a decision rather than an emergency.
#
# Requires the postgresql-client package (pg_dump, pg_restore, psql).

set -euo pipefail

DB_URL="${1:-${DATABASE_URL:-}}"

if [[ -z "$DB_URL" ]]; then
  echo "error: no database URL. Pass one as \$1 or set DATABASE_URL." >&2
  exit 1
fi

# The application uses a SQLAlchemy-flavoured URL; libpq tools do not
# understand the +driver suffix.
DB_URL="${DB_URL/+asyncpg/}"
DB_URL="${DB_URL/+psycopg2/}"

BACKUP_DIR="${BACKUP_DIR:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/tekaplay-${STAMP}.dump"

mkdir -p "$BACKUP_DIR"

echo "==> Dumping to ${OUT}"
# -Fc  custom format: compressed, and restorable table-by-table
# -Z9  maximum compression (these dumps are mostly text and compress well)
pg_dump --format=custom --compress=9 --no-owner --no-privileges \
        --file="$OUT" "$DB_URL"

echo "==> Verifying the archive is readable"
# An untested backup is a hypothesis. Listing the table of contents proves the
# archive is not truncated or corrupt; it does NOT prove the data restores
# correctly — for that, see the restore drill in docs/RUNBOOK.md.
TABLES=$(pg_restore --list "$OUT" | grep -c 'TABLE DATA' || true)
SIZE=$(du -h "$OUT" | cut -f1)

if [[ "$TABLES" -eq 0 ]]; then
  echo "error: archive contains no table data — refusing to call this a backup." >&2
  exit 1
fi

echo
echo "    file    ${OUT}"
echo "    size    ${SIZE}"
echo "    tables  ${TABLES} with data"
echo
echo "Restore with:"
echo "    pg_restore --clean --if-exists --no-owner -d \"\$TARGET_DATABASE_URL\" ${OUT}"
echo
echo "Reminder: ${BACKUP_DIR}/ is gitignored. Copy this file somewhere durable"
echo "and off this machine — a backup that only exists on one laptop is not one."
