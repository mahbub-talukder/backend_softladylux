FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# pg_dump / psql 16 for scripts/backup.sh and scripts/restore.sh (SRS §9 backups). The client major
# version must match the server (postgres:16): a newer pg_dump writes SQL that PostgreSQL 16 cannot
# restore (found in the M6 restore drill), so it comes from the PostgreSQL apt repository.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl gnupg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=0
RUN pip install --no-cache-dir -r $([ "$INSTALL_DEV" = "1" ] && echo requirements-dev.txt || echo requirements.txt)
COPY . .
# Run as an unprivileged user. media/ and staticfiles/ are volumes written by the app.
RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /app/media /app/staticfiles /app/backups \
    && chown -R app:app /app/media /app/staticfiles /app/backups
USER app
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
# Workers and timeout come from the environment (see .env.production.example).
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-60} --access-logfile - --error-logfile -"]
