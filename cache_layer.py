"""
Server-side TTL memory cache for API responses.

Eliminates redundant DB queries when multiple users or auto-refresh
cycles hit the same endpoints within the TTL window.

TTL Guidelines:
- results:  10s  (scan results change only after full scan, but stay fresh)
- sector:   30s  (sector rotation data rarely changes mid-session)
- stats:    60s  (paper trade stats change only on trade close)
- dashboard: 10s (composite endpoint, same frequency as results)
- status:   5s   (lightweight, but no need to hit DB every second)
"""

import logging
from cachetools import TTLCache

log = logging.getLogger("cache")

# ── Cache Stores ────────────────────────────────────────────────────────
results_cache = TTLCache(maxsize=5, ttl=10)
sector_cache = TTLCache(maxsize=5, ttl=30)
stats_cache = TTLCache(maxsize=5, ttl=60)
dashboard_cache = TTLCache(maxsize=5, ttl=10)
status_cache = TTLCache(maxsize=5, ttl=5)


def get_or_compute(cache, key, compute_fn):
    """Return cached value or compute + store it.

    Args:
        cache: TTLCache instance
        key: Cache key string
        compute_fn: Callable that returns the value to cache

    Returns:
        The cached or freshly computed value.
    """
    if key in cache:
        return cache[key]
    value = compute_fn()
    cache[key] = value
    return value


def invalidate_results():
    """Call after scan completes to force fresh data on next request."""
    results_cache.clear()
    dashboard_cache.clear()
    log.info("Cache invalidated: results + dashboard")


def invalidate_stats():
    """Call after trade close/open to refresh stats."""
    stats_cache.clear()
    dashboard_cache.clear()
    log.info("Cache invalidated: stats + dashboard")


def invalidate_all():
    """Nuclear option — clear everything."""
    results_cache.clear()
    sector_cache.clear()
    stats_cache.clear()
    dashboard_cache.clear()
    status_cache.clear()
    log.info("Cache invalidated: ALL")
