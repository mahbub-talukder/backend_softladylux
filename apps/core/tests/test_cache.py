# Covers SRS SET-06 / THM-06 / WEB-10 across gunicorn workers: invalidation must be shared.
import pytest
from django.core.cache.backends.db import DatabaseCache
from django.core.management import call_command

pytestmark = pytest.mark.django_db
DB_CACHE = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache",
                        "LOCATION": "django_cache"}}


def test_database_cache_is_shared_between_instances(settings):
    settings.CACHES = DB_CACHE
    call_command("createcachetable")
    a = DatabaseCache("django_cache", {})  # "worker 1"
    b = DatabaseCache("django_cache", {})  # "worker 2"
    a.set("public:settings", {"v": 1}, 60)
    assert b.get("public:settings") == {"v": 1}
    b.delete_many(["public:settings"])  # worker 2 clears on save
    assert a.get("public:settings") is None  # worker 1 no longer serves a stale value


def test_production_settings_use_the_database_cache():
    from config.settings import base

    assert base.CACHES["default"]["BACKEND"].endswith("db.DatabaseCache")
