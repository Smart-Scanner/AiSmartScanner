"""
Server-side TTL memory cache for API responses.

Eliminates redundant DB queries when multiple users or auto-refresh
cycles hit the same endpoints within the TTL window.

TTL Guidelines (configurable via env vars):
- results:   10s  (scan results change only after full scan, but stay fresh)
- sector:    30s  (sector rotation data rarely changes mid-session)
- stats:     60s  (paper trade stats change only on trade close)
- dashboard: 15s  (composite endpoint — env: DASHBOARD_CACHE_TTL, range 5-30s)
- status:    15s  (env: STATUS_CACHE_TTL, recommended 15s, range 5-30s)
- news:      60s  (news articles update only after scan enrichment)
"""

import os
import time
import threading
import logging
from cachetools import TTLCache
from metrics import counters

log = logging.getLogger("cache")

# ── Cache Stores ────────────────────────────────────────────────────────
# Never hardcode TTL in code. Always read from env.
_STATUS_TTL = int(os.getenv("STATUS_CACHE_TTL", "15"))
_DASHBOARD_TTL = int(os.getenv("DASHBOARD_CACHE_TTL", "15"))

results_cache = TTLCache(maxsize=5, ttl=10)
sector_cache = TTLCache(maxsize=5, ttl=30)
stats_cache = TTLCache(maxsize=5, ttl=60)
dashboard_cache = TTLCache(maxsize=5, ttl=_DASHBOARD_TTL)
status_cache = TTLCache(maxsize=5, ttl=_STATUS_TTL)
search_cache = TTLCache(maxsize=5, ttl=60)
news_cache = TTLCache(maxsize=5, ttl=60)


# ── Cache Name Mapping ──────────────────────────────────────────────────
def _cache_name(cache):
    """Map cache instance to human-readable name for logging/metrics."""
    if cache is results_cache: return "results"
    if cache is sector_cache: return "sector"
    if cache is stats_cache: return "stats"
    if cache is dashboard_cache: return "dashboard"
    if cache is status_cache: return "status"
    if cache is search_cache: return "search"
    if cache is news_cache: return "news"
    return "unknown"


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
    name = _cache_name(cache)
    if key in cache:
        counters.inc(f"{name}_cache_hits")
        log.debug("[CACHE HIT] %s/%s", name, key)
        return cache[key]
    counters.inc(f"{name}_cache_misses")
    log.debug("[CACHE MISS] %s/%s", name, key)
    value = compute_fn()
    cache[key] = value
    return value


def invalidate_status():
    """Clear status cache (call on scan start/complete/regime change)."""
    status_cache.clear()


def invalidate_results():
    """Call after scan completes to force fresh data on next request."""
    results_cache.clear()
    dashboard_cache.clear()
    search_cache.clear()
    status_cache.clear()
    log.info("Cache invalidated: results + dashboard + search + status")


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
    cutoff = time.time() - (max_age_hours * 3600)
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


# ── Phase E: Cache Statistics Summary (every 10 minutes) ────────────────
# Thread must be daemon=True.
# Thread failures must never affect application startup.
def _cache_stats_loop():
    """Log cache hit ratios every 10 minutes. Daemon thread — non-fatal."""
    while True:
        try:
            time.sleep(600)  # 10 minutes
            parts = []
            for name in ("status", "dashboard", "search", "results"):
                hits = counters.get(f"{name}_cache_hits") or 0
                misses = counters.get(f"{name}_cache_misses") or 0
                total = hits + misses
                ratio = round((hits / total) * 100) if total > 0 else 0
                parts.append(f"{name}={ratio}%")
            log.info("[CACHE STATS] %s", " | ".join(parts))
        except Exception as exc:
            log.warning("[CACHE STATS] loop error (non-fatal): %s", exc)

try:
    _stats_thread = threading.Thread(target=_cache_stats_loop, daemon=True)
    _stats_thread.start()
except Exception:
    pass  # Thread failure must never block startup
