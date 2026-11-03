"""Public-content cache. SRS SET-06, THM-06, WEB-10: cached reads, cleared on every save."""

from django.core.cache import cache

KEYS = ("public:settings", "public:theme", "public:home")
TTL = 3600


def cached(key, builder):
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, TTL)
    return data


def invalidate():
    cache.delete_many(KEYS)
