from .base import *  # noqa: F401,F403

SECRET_KEY = "test-secret-key-that-is-at-least-32-bytes-long"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
SMS_PROVIDER = "fake"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
Q_CLUSTER = {**Q_CLUSTER, "sync": True}  # noqa: F405
