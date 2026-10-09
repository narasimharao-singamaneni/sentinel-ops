from aiocache import caches, Cache
from aiocache.serializers import JsonSerializer

from app.config import settings

caches.set_config(
    {
        "default": {
            "cache": "aiocache.SimpleMemoryCache",
            "serializer": {"class": "aiocache.serializers.JsonSerializer"},
            "plugins": [],
        }
    }
)

_cache: Cache = caches.get("default")

_hits = 0
_misses = 0
_tracked_keys: set[str] = set()


async def get_worker(cache_key: str) -> dict | None:
    data = await _cache.get(cache_key)
    return data


async def set_worker(cache_key: str, data: dict, ttl: int) -> None:
    _tracked_keys.add(cache_key)
    await _cache.set(cache_key, data, ttl=ttl)


async def invalidate_worker(cache_key: str) -> None:
    _tracked_keys.discard(cache_key)
    await _cache.delete(cache_key)


def record_cache_hit() -> None:
    global _hits
    _hits += 1


def record_cache_miss() -> None:
    """Record a lookup that went to the database and found the item.

    Deliberately NOT called for lookups that end in 404 — a nonexistent
    resource is not a cache-policy miss.
    """
    global _misses
    _misses += 1


def cache_stats() -> dict:
    return {
        "hits": _hits,
        "misses": _misses,
        "entries": len(_tracked_keys),
        "ttl": settings.CACHE_TTL,
    }
