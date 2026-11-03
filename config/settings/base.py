from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
ADMIN_URL = env("ADMIN_URL", default="dev-admin").strip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "django_q",
    "apps.core",
    "apps.accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# SRS ARC-01 (PostgreSQL 16), ARC-08 (configuration from environment, no secrets in repo).
DATABASES = {"default": env.db("DATABASE_URL")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# SRS ACC-12 (min 8 chars, hashed), §9 Security (Argon2/PBKDF2).
AUTH_USER_MODEL = "accounts.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Dhaka"  # SRS §9 Localisation
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# SRS ARC-01 (Django 5 + DRF), ARC-05 (versioned API), §7 (error shape, pagination),
# PRM-03 (default deny: endpoints are authenticated unless they opt out).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.CookieJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

# SRS ARC-05 / ARC-06: API under /api/v1/.
# CORS: the Nuxt app calls the API with credentials (HTTP-only JWT cookies).
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# JWT (SRS ARC-06, ACC-03): access + refresh tokens live in HTTP-only cookies; CSRF is enforced
# for cookie-authenticated unsafe requests. Refresh lasts 30 days (ACC-03 "remember me").
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}
AUTH_COOKIE_ACCESS = "sll_access"
AUTH_COOKIE_REFRESH = "sll_refresh"
AUTH_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=False)
AUTH_COOKIE_SAMESITE = "Lax"

# Django-Q2 on the PostgreSQL (ORM) broker. No Redis, no Celery (ARC-10).
Q_CLUSTER = {
    "name": "softladylux",
    "orm": "default",
    "workers": env.int("Q_WORKERS", default=2),
    "retry": 120,
    "timeout": 60,
    "max_attempts": 3,
    "catch_up": False,
}

# SMS provider (pluggable). "fake" only logs the OTP.
# SRS ACC-02 / §15: credentials only from env (or Settings in M4), never hard-coded.
SMS_PROVIDER = env("SMS_PROVIDER", default="fake")
MIMSMS_API_KEY = env("MIMSMS_API_KEY", default="")
MIMSMS_SENDER_ID = env("MIMSMS_SENDER_ID", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
