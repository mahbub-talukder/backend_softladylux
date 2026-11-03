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
if [ "$RUN_MIGRATIONS" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi
exec "$@"
