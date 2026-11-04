#!/usr/bin/env bash
# SoftLadyLux backup. SRS §9 Reliability, scenario 30.
#
#   backup.sh [--mode all|db-only|media-only]     (default: all)
#
# db-only    -> a PostgreSQL dump only
# media-only -> an archive of the uploaded media folder only
# all        -> both
# The last 14 backups of each kind are kept; older ones are deleted.
#
# Environment (nothing is hard-coded):
#   DATABASE_URL   PostgreSQL connection URL (needed for db-only / all)
#   MEDIA_DIR      uploaded media folder (default: ./media, or /app/media in the container)
#   BACKUP_DIR     where backups are written (default: ./backups)
#   BACKUP_KEEP    how many to keep per kind (default: 14)
# Restore: see backend/readme.md ("Backup and restore").
set -euo pipefail

MODE="all"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1 (use --mode all|db-only|media-only)" >&2; exit 2 ;;
  esac
done
case "$MODE" in
  all|db-only|media-only) ;;
  *) echo "Invalid mode '$MODE'. Use all, db-only or media-only." >&2; exit 2 ;;
esac

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${BACKUP_KEEP:-14}"
MEDIA_DIR="${MEDIA_DIR:-$([ -d /app/media ] && echo /app/media || echo ./media)}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
trap 'rm -f -- "$BACKUP_DIR"/*.partial' EXIT  # never leave a half-written backup behind

prune() {  # prune <glob-prefix> <suffix>: keep the newest $KEEP files of one kind
  local kind="$1" suffix="$2" old
  # shellcheck disable=SC2012
  old="$(ls -1t "$BACKUP_DIR"/"$kind"-*"$suffix" 2>/dev/null | tail -n +"$((KEEP + 1))" || true)"
  [ -z "$old" ] || printf '%s\n' "$old" | xargs rm -f --
}

if [ "$MODE" = "db-only" ] || [ "$MODE" = "all" ]; then
  : "${DATABASE_URL:?DATABASE_URL is required for a database backup}"
  OUT="$BACKUP_DIR/db-$STAMP.sql.gz"
  pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip > "$OUT.partial"
  mv "$OUT.partial" "$OUT"
  prune db .sql.gz
  echo "database backup: $OUT"
fi

if [ "$MODE" = "media-only" ] || [ "$MODE" = "all" ]; then
  [ -d "$MEDIA_DIR" ] || { echo "Media folder not found: $MEDIA_DIR" >&2; exit 1; }
  OUT="$BACKUP_DIR/media-$STAMP.tar.gz"
  tar -czf "$OUT.partial" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
  mv "$OUT.partial" "$OUT"
  prune media .tar.gz
  echo "media backup: $OUT"
fi
