"""
intelligence/yf_guard.py — yfinance Circuit Breaker (Phase 2)

Prevents yfinance from becoming a latency bottleneck during Fast Scan.
Hard contract: Fast Scan MUST have zero yfinance calls in the critical path.

Public API:
  yf_is_available() -> bool
  yf_record_failure()          — call on any yfinance exception
  yf_record_success()          — call on successful yfinance fetch
  yf_reset()                   — for testing only
  yf_status() -> dict

Behaviour:
  - Failure threshold: _THRESHOLD (default 10) failures
  - Cooldown period:   _COOLDOWN  (default 300 seconds)
  - After _THRESHOLD failures → circuit OPEN → yf_is_available() returns False
  - After cooldown expires  → circuit HALF-OPEN → next call allowed as probe
  - On probe success        → circuit resets to CLOSED
  - On probe failure        → cooldown resets (exponential-ish backoff)
"""

import threading
import time
import logging

log = logging.getLogger("screener")

_THRESHOLD = 10       # failures before opening the circuit
_COOLDOWN  = 300      # seconds before attempting retry (5 min)

_failure_count   = 0
_cooldown_until  = 0.0
_lock            = threading.Lock()


def yf_is_available() -> bool:
    """
    Returns True if yfinance calls are currently allowed.
    Returns False when the circuit is OPEN (cooldown active).
    """
    with _lock:
        if _failure_count < _THRESHOLD:
            return True
        now = time.time()
        if now >= _cooldown_until:
            # Cooldown expired → half-open: allow one probe
            return True
        return False


def yf_record_failure() -> None:
    """
    Call this on any yfinance exception.
    Increments failure count; opens circuit when threshold exceeded.
    """
    global _failure_count, _cooldown_until
    with _lock:
        _failure_count += 1
        if _failure_count >= _THRESHOLD:
            _cooldown_until = time.time() + _COOLDOWN
            log.warning(
                "yf_guard: Circuit OPEN after %d failures. "
                "yfinance suspended for %.0fs.",
                _failure_count, _COOLDOWN,
            )


def yf_record_success() -> None:
    """
    Call this after a successful yfinance fetch.
    Decrements the failure counter (floor at 0), resets circuit if it was half-open.
    """
    global _failure_count, _cooldown_until
    with _lock:
        was_open = _failure_count >= _THRESHOLD
        _failure_count = max(0, _failure_count - 1)
        if was_open and _failure_count < _THRESHOLD:
            _cooldown_until = 0.0
            log.info("yf_guard: Circuit CLOSED — yfinance recovered.")


def yf_reset() -> None:
    """Reset circuit breaker state. For use in testing only."""
    global _failure_count, _cooldown_until
    with _lock:
        _failure_count = 0
        _cooldown_until = 0.0


def yf_status() -> dict:
    """Return current circuit breaker state for /api/health and diagnostics."""
    with _lock:
        now = time.time()
        remaining = max(0.0, _cooldown_until - now)
        circuit_open = _failure_count >= _THRESHOLD and remaining > 0
        return {
            "yf_available": not circuit_open,
            "yf_failure_count": _failure_count,
            "yf_cooldown_remaining_s": round(remaining),
            "yf_circuit_open": circuit_open,
        }

import os
import random
import requests

def get_yf_session() -> requests.Session:
    session = requests.Session()
    proxies_env = os.environ.get("YFINANCE_PROXIES", "")
    if proxies_env:
        proxy_list = [p.strip() for p in proxies_env.split(",") if p.strip()]
        if proxy_list:
            proxy = random.choice(proxy_list)
            session.proxies.update({"http": proxy, "https": proxy})
            log.debug("yf_guard: Using proxy %s", proxy)
    return session
