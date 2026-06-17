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
# WARNING: Cache metrics and stores are process-local (in-memory).
# If Gunicorn is scaled to multiple workers/containers, cache hits/misses will
# be tracked independently per-process.
#
# Standard safety TTL guards:
# results: 1h, dashboard: 1h, sector: 1h, stats: 1h, search: 30m, news: 15m.
results_cache = TTLCache(maxsize=5, ttl=int(os.getenv("RESULTS_CACHE_TTL", "3600")))
sector_cache = TTLCache(maxsize=5, ttl=int(os.getenv("SECTOR_CACHE_TTL", "3600")))
stats_cache = TTLCache(maxsize=5, ttl=int(os.getenv("STATS_CACHE_TTL", "5")))
dashboard_cache = TTLCache(maxsize=5, ttl=int(os.getenv("DASHBOARD_CACHE_TTL", "3600")))
search_cache = TTLCache(maxsize=5, ttl=int(os.getenv("SEARCH_CACHE_TTL", "1800")))
news_cache = TTLCache(maxsize=5, ttl=int(os.getenv("NEWS_CACHE_TTL", "900")))

# Custom Status Cache Store (indefinite idle cache, 15s refresh on scan, double-checked locking)
_status_cache_value = None
_status_cache_time = 0.0
_status_recompute_lock = threading.Lock()


# ── Cache Name Mapping ──────────────────────────────────────────────────
def _cache_name(cache):
    """Map cache instance to human-readable name for logging/metrics."""
    if cache is results_cache: return "results"
    if cache is sector_cache: return "sector"
    if cache is stats_cache: return "stats"
    if cache is dashboard_cache: return "dashboard"
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


def _is_status_valid(cache_time):
    """Check if the current status cache entry is still fresh.

    Returns True if cached value should be served as-is.
    """
    if cache_time == 0.0:
        return False
    age = time.time() - cache_time
    if age >= 86400:  # 24-hour hard expiry guard
        return False
    import db
    active, _ = db.is_scan_active()
    if active and age >= 15:  # 15s refresh during active scans
        return False
    return True


def get_status_cache(compute_fn):
    """Get the scan status from the cache, computing it if not cached or expired.

    Uses double-checked locking and custom expiration logic:
    - Idle: cached indefinitely (up to 24h safety limit)
    - Scanning: refresh every 15 seconds
    - State change: invalidates cache immediately via event
    - Corruption guard: if compute_fn fails and stale cache exists, return stale
    """
    global _status_cache_value, _status_cache_time

    # 1. Lock-free read path
    if _status_cache_value is not None and _is_status_valid(_status_cache_time):
        counters.inc("status_cache_hits")
        log.debug("[STATUS CACHE HIT] age=%ds", int(time.time() - _status_cache_time))
        return _status_cache_value

    # 2. Cache miss or expired — acquire lock to avoid cache stampede
    with _status_recompute_lock:
        # Double-check: another thread may have refreshed while we waited
        if _status_cache_value is not None and _is_status_valid(_status_cache_time):
            counters.inc("status_cache_hits")
            log.debug("[STATUS CACHE HIT - DOUBLE CHECKED] age=%ds", int(time.time() - _status_cache_time))
            return _status_cache_value

        counters.inc("status_cache_misses")
        log.debug("[STATUS CACHE MISS] Recomputing status...")

        # 3. Corruption guard: if compute fails and stale value exists, serve stale
        try:
            _status_cache_value = compute_fn()
            _status_cache_time = time.time()
        except Exception as exc:
            if _status_cache_value is not None:
                log.warning(
                    "[STATUS CACHE] Refresh failed, serving stale cache (age=%ds): %s",
                    int(time.time() - _status_cache_time), exc,
                )
                return _status_cache_value
            log.error("[STATUS CACHE] Refresh failed with no stale fallback: %s", exc, exc_info=True)
            raise

        return _status_cache_value


def warm_status_cache(compute_fn):
    """Warm up status cache on application startup.

    Designed to be called from a daemon thread so that warm-up failure
    or slowness NEVER blocks or fails application startup.

    Usage:
        threading.Thread(target=cache_layer.warm_status_cache,
                         args=(compute_fn,), daemon=True).start()
    """
    global _status_cache_value, _status_cache_time
    log.info("[CACHE WARMUP] Initializing status cache...")
    try:
        with _status_recompute_lock:
            _status_cache_value = compute_fn()
            _status_cache_time = time.time()
        log.info("[CACHE WARMUP] Status cache pre-populated successfully")
    except Exception as exc:
        log.warning("[CACHE WARMUP] Failed to warm status cache (non-fatal): %s", exc)


def get_cache_metrics() -> dict:
    """Return metrics for results, dashboard, search, and status caches.

    WARNING: Cache metrics and stores are process-local (in-memory).
    If Gunicorn is scaled to multiple processes/containers, metrics will be
    tracked independently per process.
    """
    metrics = {}
    for name in ("status", "dashboard", "search", "results"):
        hits = counters.get(f"{name}_cache_hits") or 0
        misses = counters.get(f"{name}_cache_misses") or 0
        invalidations = counters.get(f"{name}_cache_invalidations") or 0

        total = hits + misses
        # Divide-by-zero protection
        hit_rate = round((hits / total) * 100, 1) if total > 0 else 0.0

        metrics[f"{name}_hits"] = hits
        metrics[f"{name}_misses"] = misses
        metrics[f"{name}_invalidations"] = invalidations
        metrics[f"{name}_hit_rate"] = hit_rate

    status_age = None
    if _status_cache_value is not None:
        status_age = round(time.time() - _status_cache_time)
    metrics["status_age_seconds"] = status_age

    return metrics


def invalidate_status():
    """Clear status cache (call on scan start/complete/regime change)."""
    global _status_cache_value, _status_cache_time
    with _status_recompute_lock:
        _status_cache_value = None
        _status_cache_time = 0.0
    counters.inc("status_cache_invalidations")
    log.debug("Cache invalidated: status")


def invalidate_results():
    """Call after scan completes to force fresh data on next request."""
    results_cache.clear()
    counters.inc("results_cache_invalidations")
    dashboard_cache.clear()
    counters.inc("dashboard_cache_invalidations")
    search_cache.clear()
    counters.inc("search_cache_invalidations")
    invalidate_status()
    log.info("Cache invalidated: results + dashboard + search + status")


def invalidate_stats():
    """Call after trade close/open to refresh stats."""
    stats_cache.clear()
    counters.inc("stats_cache_invalidations")
    dashboard_cache.clear()
    counters.inc("dashboard_cache_invalidations")
    log.info("Cache invalidated: stats + dashboard")


def invalidate_all():
    """Nuclear option — clear everything."""
    results_cache.clear()
    counters.inc("results_cache_invalidations")
    sector_cache.clear()
    counters.inc("sector_cache_invalidations")
    stats_cache.clear()
    counters.inc("stats_cache_invalidations")
    dashboard_cache.clear()
    counters.inc("dashboard_cache_invalidations")
    search_cache.clear()
    counters.inc("search_cache_invalidations")
    news_cache.clear()
    counters.inc("news_cache_invalidations")
    invalidate_status()
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
