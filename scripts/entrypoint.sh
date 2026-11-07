#!/bin/sh
set -e
# Wait for the database, then migrate for the web process and hand over to the command.
# The worker (`qcluster`) shares this image and must not migrate.
python - <<'PY'
import os, time, psycopg
url = os.environ["DATABASE_URL"]
for _ in range(30):
    try:
        psycopg.connect(url).close(); break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("database not reachable")
PY
# A new named volume is owned by root. Production starts as root, gives the data directories to app,
# then continues as app. Dev stays root because the source tree is bind-mounted from the host.
if [ "$(id -u)" = "0" ] && [ "$DJANGO_SETTINGS_MODULE" = "config.settings.prod" ]; then
  mkdir -p /app/media /app/staticfiles /app/backups
  chown app:app /app/media /app/staticfiles /app/backups
  chmod 755 /app/media /app/staticfiles /app/backups
  exec runuser -u app -- "$0" "$@"
fi
# M6: a production container refuses to start on unsafe configuration (apps/core/envcheck.py).
if [ "$DJANGO_SETTINGS_MODULE" = "config.settings.prod" ]; then
  python -m apps.core.envcheck
fi
migrate=1
if [ "$RUN_MIGRATIONS" != "1" ]; then
  migrate=0
fi
for arg in "$@"; do
  if [ "$arg" = "qcluster" ]; then
    migrate=0
    break
  fi
done
if [ "$migrate" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py createcachetable
  python manage.py collectstatic --noinput
fi
exec "$@"
