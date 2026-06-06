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
- news:     60s  (news articles update only after scan enrichment)
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
search_cache = TTLCache(maxsize=5, ttl=60)
news_cache = TTLCache(maxsize=5, ttl=60)

# ── Rate Limiting ───────────────────────────────────────────────────────
# WARNING: This limiter is PROCESS-LOCAL (in-memory TTLCache).
# Safe ONLY for gunicorn --workers 1 --threads N (current Procfile).
# If workers > 1, each worker gets its own limiter → rate limit bypassed.
# Migrate to DB-backed limiter (db.get_meta/set_meta) if scaling to workers > 1.
custom_scan_limiter = TTLCache(maxsize=1, ttl=10)  # 10s cooldown



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
    search_cache.clear()
    log.info("Cache invalidated: results + dashboard + search")


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
    search_cache.clear()
    news_cache.clear()
    log.info("Cache invalidated: ALL")


# ── Detail Cache Cleanup ────────────────────────────────────────────────
import time as _time
from pathlib import Path as _Path

def cleanup_detail_cache(cache_dir="cache/detail", max_age_hours=72):
    """Remove stale detail cache files older than max_age.
    Called after each scan to prevent unbounded disk growth.
    72h retention covers weekend market closures.
    Safe to call from scanner.py without circular imports.
    """
    d = _Path(cache_dir)
    if not d.exists():
        return 0
    cutoff = _time.time() - (max_age_hours * 3600)
    removed = 0
    for f in d.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("Detail cache cleanup: removed %d stale files (>%dh old)", removed, max_age_hours)
    return removed
