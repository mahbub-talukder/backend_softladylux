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

## Notes
- Auth: JWT in HTTP-only cookies with CSRF checks (ARC-06). CORS origins from `CORS_ALLOWED_ORIGINS`.
- SMS: `SMS_PROVIDER=fake` (default) only logs OTPs. MiMSMS is added behind the same interface in M4.
- Comments cite SRS requirement IDs; see `../docs/TRACEABILITY.md`.
