FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# pg_dump for scripts/backup.sh (SRS §9 backups)
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=0
RUN pip install --no-cache-dir -r $([ "$INSTALL_DEV" = "1" ] && echo requirements-dev.txt || echo requirements.txt)
COPY . .
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
