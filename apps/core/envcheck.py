"""Production environment check. SRS §9 Security; M6 release safety.

Refuses to start a production container on unsafe or unfinished configuration (placeholder secrets,
DEBUG on, open ALLOWED_HOSTS, default admin path, insecure cookies). Run by scripts/entrypoint.sh
when DJANGO_SETTINGS_MODULE is config.settings.prod; also usable by hand:
    python -m apps.core.envcheck
Exit code 0 = ok (warnings are printed but do not block), 1 = unsafe."""

import os
import sys
from collections.abc import Mapping

PLACEHOLDERS = ("change-me", "changeme", "replace", "example", "your-", "xxxx", "secret-key")
DEFAULT_ADMIN_PATHS = {"", "dev-admin", "admin", "change-this-obscure-path"}


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


def _placeholder(v: str) -> bool:
    low = v.lower()
    return any(p in low for p in PLACEHOLDERS)


def problems(env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a production environment."""
    errors: list[str] = []
    warnings: list[str] = []
    g = env.get

    secret = g("SECRET_KEY", "")
    if len(secret) < 40 or _placeholder(secret):
        errors.append(
            "SECRET_KEY must be a long random value (40+ characters), not a placeholder. Generate "
            "one: python3 -c \"import secrets; print(secrets.token_urlsafe(64))\"")
    if _bool(g("DEBUG"), False):
        errors.append("DEBUG must be false in production.")
    hosts = [h.strip() for h in g("ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not hosts or "*" in hosts:
        errors.append("ALLOWED_HOSTS must list your real domain(s) and must not be empty or '*'.")
    admin = g("ADMIN_URL", "").strip("/ ")
    if admin in DEFAULT_ADMIN_PATHS or len(admin) < 12 or _placeholder(admin):
        errors.append(
            "ADMIN_URL must be a long unguessable path (12+ characters), not the default.")
    if not _bool(g("COOKIE_SECURE"), True):
        errors.append("COOKIE_SECURE must be true in production (HTTPS-only cookies).")
    origins = [o.strip() for o in g("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
    if not origins or any(not o.startswith("https://") for o in origins):
        errors.append("CSRF_TRUSTED_ORIGINS must list your https:// origin(s).")
    for key in ("POSTGRES_PASSWORD", "DATABASE_URL"):
        if not g(key) or _placeholder(g(key, "")):
            errors.append(f"{key} is missing or still a placeholder.")
    if g("DJANGO_SETTINGS_MODULE", "") != "config.settings.prod":
        errors.append("DJANGO_SETTINGS_MODULE must be config.settings.prod.")

    if not _bool(g("SSL_REDIRECT"), True):
        warnings.append("SSL_REDIRECT is off: HTTP requests are not redirected by Django.")
    if int(g("HSTS_SECONDS", "31536000") or 0) <= 0:
        warnings.append("HSTS is disabled (HSTS_SECONDS=0).")
    if "console" in g("EMAIL_BACKEND", "console"):
        warnings.append("EMAIL_BACKEND is the console backend: no email will be sent.")
    if g("SMS_PROVIDER", "fake") == "fake":
        warnings.append("SMS_PROVIDER=fake: OTP codes are only logged, customers get no SMS.")
    elif g("SMS_PROVIDER") == "mimsms" and not g("MIMSMS_ENDPOINT"):
        warnings.append("SMS_PROVIDER=mimsms but MIMSMS_ENDPOINT is empty in the environment; "
                        "make sure Admin → Settings → SMS is filled in.")
    if not g("NUXT_PUBLIC_SITE_URL"):
        warnings.append("NUXT_PUBLIC_SITE_URL is empty: sitemap and canonical URLs use the request "
                        "host.")
    return errors, warnings


def main() -> int:
    errors, warnings = problems(os.environ)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if errors:
        print(f"\nRefusing to start: {len(errors)} configuration problem(s). See "
              "backend/.env.production.example.", file=sys.stderr)
        return 1
    print("environment check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
