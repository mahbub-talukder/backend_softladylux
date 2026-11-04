from .base import *  # noqa: F401,F403

# SRS §9 Security: HTTPS only, HSTS, secure/HTTP-only/SameSite cookies.
DEBUG = False
# Internal service names used by the frontend's server-side requests (http://backend:8000), the
# container healthchecks and local tooling. Public hosts come from ALLOWED_HOSTS in the environment.
for _host in ("backend", "localhost", "127.0.0.1"):
    if _host not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS.append(_host)  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=True)  # noqa: F405
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
AUTH_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = env.int("HSTS_SECONDS", default=31536000)  # noqa: F405
# HSTS covers this host only. includeSubDomains and preload are deliberately off: they would force
# HTTPS on every other subdomain of the owner's domain and are hard to undo. Turn them on only when
# you are sure (then set these two and remove the silenced checks below).
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("HSTS_INCLUDE_SUBDOMAINS", default=False)  # noqa: F405
SECURE_HSTS_PRELOAD = env.bool("HSTS_PRELOAD", default=False)  # noqa: F405
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]  # the deliberate choice above
SECURE_SSL_REDIRECT = env.bool("SSL_REDIRECT", default=True)  # noqa: F405
SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]
