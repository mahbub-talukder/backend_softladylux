#!/bin/sh
set -e
# Wait for the database, then migrate (backend only) and hand over to the command.
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
# M6: a production container refuses to start on unsafe configuration (apps/core/envcheck.py).
if [ "$DJANGO_SETTINGS_MODULE" = "config.settings.prod" ]; then
  python -m apps.core.envcheck
fi
if [ "$RUN_MIGRATIONS" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py createcachetable
  python manage.py collectstatic --noinput
fi
exec "$@"
