import os
import time
import logging
from datetime import datetime, timedelta
import threading

from db import get_historical_cache, set_historical_cache
from data_provider import provider_manager

log = logging.getLogger("screener")

# Cache Stampede Protection Locks
_fetch_locks = {}
_lock_guard = threading.Lock()

# Failure Backoff Tracking
_refresh_backoff = {}  # token -> timestamp
BACKOFF_MINUTES = 15
HIST_CACHE_TTL_HOURS = int(os.environ.get("HIST_CACHE_TTL_HOURS", "24"))

def _record_provider_stat(provider_name: str):
    """Record historical_calls for the provider to satisfy Provider Utilization Audit."""
    try:
        from db import get_pg_connection, _get_connection
        if is_postgresql():
            with get_pg_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS provider_stats (
                            provider_name TEXT PRIMARY KEY,
                            historical_calls INTEGER DEFAULT 0
                        )
                    ''')
                    cursor.execute('''
                        INSERT INTO provider_stats (provider_name, historical_calls)
                        VALUES (%s, 1)
                        ON CONFLICT (provider_name) DO UPDATE SET historical_calls = provider_stats.historical_calls + 1
                    ''', (provider_name,))
                conn.commit()
        else:
            with _get_connection() as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS provider_stats (
                        provider_name TEXT PRIMARY KEY,
                        historical_calls INTEGER DEFAULT 0
                    )
                ''')
                conn.execute('''
                    INSERT INTO provider_stats (provider_name, historical_calls)
                    VALUES (?, 1)
                    ON CONFLICT(provider_name) DO UPDATE SET historical_calls = provider_stats.historical_calls + 1
                ''', (provider_name,))
                conn.commit()
    except Exception as e:
        log.error(f"[HistoricalService] Failed to record provider stat: {e}")

def get_daily_history(symbol_token: str, days: int, exchange: str = "NSE", allow_stale: bool = True) -> list:
    """
    Service abstraction for fetching historical data.
    Implements Cache, Stampede Protection, Backoff, and Stale Fallback.
    """
    timeframe = f"{days}D"
    
    # 1. DB Cache Hit? (Strict check)
    cached_data = get_historical_cache(symbol_token, exchange, timeframe, allow_stale=False)
    if cached_data:
        return cached_data

    # 2. Acquire token-specific Lock (Stampede protection)
    with _lock_guard:
        if symbol_token not in _fetch_locks:
            _fetch_locks[symbol_token] = threading.Lock()
        token_lock = _fetch_locks[symbol_token]

    with token_lock:
        # 3. Double-checked locking
        cached_data = get_historical_cache(symbol_token, exchange, timeframe, allow_stale=False)
        if cached_data:
            return cached_data

        # 4. Check Backoff
        if symbol_token in _refresh_backoff:
            backoff_until = _refresh_backoff[symbol_token]
            if time.time() < backoff_until:
                log.debug(f"[HistoricalService] Backoff active for {symbol_token}. Serving stale.")
                return _serve_stale_fallback(symbol_token, exchange, timeframe, allow_stale)
            else:
                del _refresh_backoff[symbol_token]

        # 5. Acquire RESEARCH provider
        provider = provider_manager.acquire_active_provider(required_role="RESEARCH")
        if not provider:
            log.warning(f"[HistoricalService] No active RESEARCH provider for {symbol_token}. Serving stale.")
            return _serve_stale_fallback(symbol_token, exchange, timeframe, allow_stale)
        
        try:
            fromdate = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
            todate = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # Fetch via Angel API
            data = provider.fetch_historical(symbol_token, exchange=exchange, fromdate=fromdate, todate=todate, interval="ONE_DAY")
            
            # 6. If Failure
            if not data:
                log.warning(f"[HistoricalService] Fetch failed for {symbol_token}. Activating {BACKOFF_MINUTES}m backoff.")
                _refresh_backoff[symbol_token] = time.time() + (BACKOFF_MINUTES * 60)
                return _serve_stale_fallback(symbol_token, exchange, timeframe, allow_stale)
            
            # 7. If Success
            set_historical_cache(symbol_token, exchange, timeframe, data, ttl_hours=HIST_CACHE_TTL_HOURS)
            _record_provider_stat(provider.name)
            
            return data
            
        except Exception as e:
            log.error(f"[HistoricalService] Exception fetching {symbol_token}: {e}")
            _refresh_backoff[symbol_token] = time.time() + (BACKOFF_MINUTES * 60)
            return _serve_stale_fallback(symbol_token, exchange, timeframe, allow_stale)
        finally:
            provider_manager.release_provider(provider)

def _serve_stale_fallback(symbol_token: str, exchange: str, timeframe: str, allow_stale: bool):
    if not allow_stale:
        return None
    stale_data = get_historical_cache(symbol_token, exchange, timeframe, allow_stale=True)
    if stale_data:
        log.warning(f"[HistoricalService] Serving STALE cache for {symbol_token} as fallback.")
        return stale_data
    return None
