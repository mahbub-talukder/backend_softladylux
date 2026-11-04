# SoftLadyLux backend

Django 5 + DRF + PostgreSQL 16 + Django-Q2 (PostgreSQL broker). Requirements: `docs/SRS.md`.

## Run (Docker): always from the project root (parent of `backend/` and `frontend/`)
```
cp backend/.env.example .env     # fill in secrets; never commit .env
docker compose -f backend/deploy/docker-compose.yml -f backend/deploy/docker-compose.dev.yml up --build
```
Services: `db`, `backend`, `worker`, `frontend`, `nginx` (http://localhost:8080). Health: `/api/v1/health/`.
Compose files and `nginx/` live in `backend/deploy/`.

## Test
```
# from the project root:
docker compose -f backend/deploy/docker-compose.yml -f backend/deploy/docker-compose.dev.yml run --rm -e RUN_MIGRATIONS=0 \
  -e DJANGO_SETTINGS_MODULE=config.settings.test backend sh -c "ruff check . && pytest"
```
The route-coverage gate (`conftest.py`) fails the run if any `/api/v1/` route+method has no test (SRS §9, scenario 24). Use `ROUTE_GATE=0` for partial runs.

## Backup and restore
`scripts/backup.sh --mode all|db-only|media-only` (default `all`; SRS §9, scenario 30). `db-only` writes a gzipped
`pg_dump`, `media-only` a `tar.gz` of the media folder, `all` both. The newest 14 of each kind are kept.
Settings come from the environment: `DATABASE_URL`, `MEDIA_DIR`, `BACKUP_DIR`, `BACKUP_KEEP`.
```
docker compose -f backend/deploy/docker-compose.yml exec backend scripts/backup.sh --mode all
```
Schedule it daily with `--mode all` (cron on the host, or your platform's scheduler).

Restore (stop the app first, restore into an empty database):
```
gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | psql "$DATABASE_URL"
tar -xzf backups/media-YYYYMMDD-HHMMSS.tar.gz -C /path/containing/media
python manage.py migrate && python manage.py createcachetable
```
Test a restore on a copy before you need it. The full migration and restore runbook arrives in M6.

## Notes
- Auth: JWT in HTTP-only cookies with CSRF checks (ARC-06). CORS origins from `CORS_ALLOWED_ORIGINS`.
- SMS: `SMS_PROVIDER=fake` (default) only logs OTPs. MiMSMS is available behind the same interface (`SMS_PROVIDER=mimsms`); its HTTP details are configuration (see `.env.example`) and it is unverified until a real account exists.
- Comments cite SRS requirement IDs; see `../docs/TRACEABILITY.md`.
