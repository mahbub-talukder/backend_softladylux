#!/usr/bin/env bash
# SoftLadyLux restore. SRS §9 Reliability; M6 runbook.
#
#   restore.sh --db  <db-YYYYMMDD-HHMMSS.sql.gz>  [--force]
#   restore.sh --media <media-YYYYMMDD-HHMMSS.tar.gz> [--force]
#
# --db     loads a database dump into $DATABASE_URL. Refuses a database that already has tables
#          unless --force (restoring over live data should be a deliberate act).
# --media  unpacks a media archive into the parent of $MEDIA_DIR (it contains the folder itself).
#          Refuses a non-empty media folder unless --force.
# Stop the app first. After a database restore run: python manage.py migrate
set -euo pipefail

KIND="" FILE="" FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --db) KIND=db; FILE="${2:-}"; shift 2 ;;
    --media) KIND=media; FILE="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$KIND" ] || { echo "Choose --db <file> or --media <file>." >&2; exit 2; }
[ -f "$FILE" ] || { echo "Backup file not found: ${FILE:-<none>}" >&2; exit 1; }
gzip -t "$FILE" 2>/dev/null || { echo "Not a valid gzip file: $FILE" >&2; exit 1; }

if [ "$KIND" = "db" ]; then
  : "${DATABASE_URL:?DATABASE_URL is required}"
  tables="$(psql "$DATABASE_URL" -Atqc "select count(*) from information_schema.tables where table_schema='public'")"
  if [ "$tables" != "0" ] && [ "$FORCE" != "1" ]; then
    echo "The database already has $tables tables. Restore into an empty database, or pass --force." >&2
    exit 1
  fi
  gunzip -c "$FILE" | psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q >/dev/null
  echo "database restored from $FILE (now run: python manage.py migrate)"
else
  MEDIA_DIR="${MEDIA_DIR:-$([ -d /app/media ] && echo /app/media || echo ./media)}"
  if [ -d "$MEDIA_DIR" ] && [ -n "$(ls -A "$MEDIA_DIR" 2>/dev/null)" ] && [ "$FORCE" != "1" ]; then
    echo "The media folder $MEDIA_DIR is not empty. Pass --force to overwrite files." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$MEDIA_DIR")"
  tar -xzf "$FILE" -C "$(dirname "$MEDIA_DIR")"
  echo "media restored from $FILE into $MEDIA_DIR"
fi
