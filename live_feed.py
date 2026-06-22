"""
Angel One SmartAPI WebSocket Live Feed
Real-time tick data for Smart Screener
"""

import os
import json
import time
import random
import logging
import threading
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

import pyotp
import requests
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from metrics.timer import timed
from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session

log = logging.getLogger("live_feed")

ENV_FILE = Path(__file__).parent / ".env"
TOKEN_FILE = Path(__file__).parent / "cache" / "angel_tokens.json"

_angel_accounts = []
_active_account_idx = 0
_account_lock = threading.Lock()

_smart_api = None # For legacy reference, though we use get_smart_api()
_auth_token = None
_feed_token = None
_last_login = 0

_token_map = {}
_reverse_map = {}
_session_lock = threading.Lock()

_live_prices = {}
_prices_lock = threading.Lock()

_subscribers = set()
_ws_thread = None
_ws_running = False
_sws = None

_correlation_id = "smartscanner"
_WS_MODE = 2  # 2 = Quote Mode (contains open, high, low, close, volume)
MAX_WS_TOKENS_PER_SESSION = 1000
MAX_WS_BATCH_SIZE = 50

REST_GAP_SECONDS = 0.5
_hist_lock = threading.Lock()
_hist_last_call = 0.0

# Phase 4: Dynamic throttling state — tracks recent Angel API 429 failures
_angel_429_count = 0
_angel_429_window_start = 0.0
_ANGEL_429_WINDOW_SECS = 600  # 10 minute sliding window
_ANGEL_429_LOCK = threading.Lock()


def get_active_account():
    with _account_lock:
        if not _angel_accounts:
            return None
        return _angel_accounts[_active_account_idx]

def switch_account(reason=""):
    global _active_account_idx, _smart_api, _feed_token
    with _account_lock:
        if not _angel_accounts:
            return False
        _active_account_idx = (_active_account_idx + 1) % len(_angel_accounts)
        acct = _angel_accounts[_active_account_idx]
        _smart_api = acct["smart_api"]
        _feed_token = acct["feed_token"]
        log.warning("Switched to Angel Account %d due to: %s", acct['id'], reason)
        return True

def get_smart_api():
    acct = get_active_account()
    return acct["smart_api"] if acct else None

def _record_429():
    acct = get_active_account()
    if not acct: return
    now = time.time()
    if now - acct["429_window_start"] > _ANGEL_429_WINDOW_SECS:
        acct["429_count"] = 0
        acct["429_window_start"] = now
    acct["429_count"] += 1
    log.warning("Account %d hit 429 rate limit (count: %d).", acct['id'], acct['429_count'])
    if acct["429_count"] >= 3:
        switch_account(reason="Rate Limit Exceeded (3+ 429s)")

def get_dynamic_rest_gap() -> float:
    acct = get_active_account()
    if not acct: return REST_GAP_SECONDS
    now = time.time()
    if now - acct["429_window_start"] > _ANGEL_429_WINDOW_SECS:
        return REST_GAP_SECONDS
    count = acct["429_count"]
    if count <= 0: return REST_GAP_SECONDS
    elif count <= 2: return 1.0
    elif count <= 4: return 1.5
    else: return 2.0

# ── P0: Angel Reauth Storm Lock ─────────────────────────────────────────
# When AG8001 (Invalid Token) is detected by multiple threads simultaneously,
# they must NOT all call _login(). This lock + cooldown prevents login storms.
_reauth_lock = threading.Lock()
_last_reauth_time = 0.0
_last_reauth_success = False
_REAUTH_COOLDOWN_SECS = 60  # Minimum seconds between re-auth attempts


def force_reauth(reason: str = "unknown") -> bool:
    """Force a re-authentication with storm prevention.

    Only one thread can re-auth at a time. If another reauth happened
    within the last 60 seconds, the current request is skipped.
    Tracks success state for observability.

    Returns True if reauth succeeded, False otherwise.
    """
    global _last_reauth_time, _last_reauth_success
    with _reauth_lock:
        now = time.time()
        if now - _last_reauth_time < _REAUTH_COOLDOWN_SECS:
            log.info("[REAUTH_SKIP] reason=%s — last reauth was %.0fs ago (cooldown=%ds, last_success=%s)",
                     reason, now - _last_reauth_time, _REAUTH_COOLDOWN_SECS, _last_reauth_success)
            return _last_reauth_success

        log.warning("[REAUTH_START] reason=%s — initiating forced re-login", reason)
        _last_reauth_time = now

        # Increment reauth counter
        try:
            import db
            db.increment_mem_counter("angel_reauth_count")
        except Exception:
            pass

        # Force fresh login by clearing existing session
        pass
        _auth_token = None
        _feed_token = None
        _last_login = 0

        success = _login()
        _last_reauth_success = success

        log.info("[REAUTH_COMPLETE] reason=%s success=%s", reason, success)
        return success

_IST = timezone(timedelta(hours=5, minutes=30))

def _load_env():
    global _angel_accounts
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        log.info("Loaded .env file from %s", ENV_FILE)

    # Parse ANGEL_API_KEY_1, ANGEL_API_KEY_2, etc. or fallback to ANGEL_API_KEY
    _angel_accounts = []
    
    # Check for numbered accounts
    for i in range(1, 10):
        ak = os.environ.get(f"PROVIDER_{i}_API_KEY") or os.environ.get(f"ANGEL_API_KEY_{i}")
        if ak:
            _angel_accounts.append({
                "id": i,
                "api_key": ak,
                "client_id": os.environ.get(f"PROVIDER_{i}_CLIENT_ID", "") or os.environ.get(f"ANGEL_CLIENT_ID_{i}", ""),
                "mpin": os.environ.get(f"PROVIDER_{i}_MPIN", "") or os.environ.get(f"ANGEL_MPIN_{i}", ""),
                "totp_secret": os.environ.get(f"PROVIDER_{i}_TOTP_SECRET", "") or os.environ.get(f"PROVIDER_{i}_TOTP", "") or os.environ.get(f"ANGEL_TOTP_SECRET_{i}", ""),
                "smart_api": None,
                "last_login": 0,
                "429_count": 0,
                "429_window_start": 0.0,
                "login_failures": 0,
                "cooldown_until": 0.0,
                "circuit_broken": False,
                "circuit_error": "",
                "feed_token": None
            })
            
    # Fallback to single account
    if not _angel_accounts:
        ak = os.environ.get("PROVIDER_3_API_KEY") or os.environ.get("ANGEL_API_KEY")
        if ak:
            _angel_accounts.append({
                "id": 1,
                "api_key": ak,
                "client_id": os.environ.get("PROVIDER_3_CLIENT_ID", "") or os.environ.get("ANGEL_CLIENT_ID", ""),
                "mpin": os.environ.get("PROVIDER_3_MPIN", "") or os.environ.get("ANGEL_MPIN", ""),
                "totp_secret": os.environ.get("PROVIDER_3_TOTP_SECRET", "") or os.environ.get("PROVIDER_3_TOTP", "") or os.environ.get("ANGEL_TOTP_SECRET", ""),
                "smart_api": None,
                "last_login": 0,
                "429_count": 0,
                "429_window_start": 0.0,
                "login_failures": 0,
                "cooldown_until": 0.0,
                "circuit_broken": False,
                "circuit_error": "",
                "feed_token": None
            })

    log.info("Loaded %d Angel One accounts for load balancing", len(_angel_accounts))

_load_env()

def load_token_map():
    global _token_map, _reverse_map
    if TOKEN_FILE.exists():
        try:
            _token_map = json.loads(TOKEN_FILE.read_text())
            _reverse_map = {v: k for k, v in _token_map.items()}
            log.info("Loaded %d symbol tokens", len(_token_map))
            return
        except Exception as exc:
            log.warning("Token file load failed: %s", exc)
    refresh_token_map()

def refresh_token_map():
    global _token_map, _reverse_map
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        data = requests.get(url, timeout=30).json()
        nse_eq = [d for d in data if d.get("exch_seg") == "NSE" and d.get("symbol", "").endswith("-EQ")]
        _token_map = {d["symbol"].replace("-EQ", ""): d["token"] for d in nse_eq}
        _reverse_map = {v: k for k, v in _token_map.items()}
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(_token_map))
        log.info("Refreshed %d symbol tokens", len(_token_map))
    except Exception as exc:
        log.error("Token refresh failed: %s", exc)

def get_token(symbol: str):
    import db
    if not _token_map:
        load_token_map()
    resolved = db.resolve_symbol(symbol)
    return _token_map.get(resolved.upper().replace(".NS", ""))

def get_symbol(token: str):
    return _reverse_map.get(str(token))

_circuit_lock = threading.Lock()

def reset_login_circuit_breaker(force_retry=True):
    with _circuit_lock:
        for acct in _angel_accounts:
            acct["login_failures"] = 1 if force_retry else 0
            acct["cooldown_until"] = 0.0
            acct["circuit_broken"] = False
            acct["circuit_error"] = ""
        log.info("Angel login circuit breaker has been reset for all %d accounts.", len(_angel_accounts))
        try:
            import db
            db.set_meta("angel_login_status", {
                "status": "reset",
                "failures": 1 if force_retry else 0,
                "message": "Reset (Single retry allowed)",
                "circuit_broken": False,
                "cooldown_until": 0.0,
                "error_details": ""
            })
        except Exception:
            pass

def _login_account(acct):
    if not all([acct["api_key"], acct["client_id"], acct["mpin"], acct["totp_secret"]]):
        log.error("Account %d credentials missing", acct['id'])
        return False
        
    now = time.time()
    if acct["circuit_broken"]:
        log.warning("Account %d circuit broken. Error: %s", acct['id'], acct["circuit_error"])
        return False
    if now < acct["cooldown_until"]:
        log.warning("Account %d in cooldown for %d seconds", acct['id'], int(acct["cooldown_until"] - now))
        return False

    try:
        totp = pyotp.TOTP(acct["totp_secret"]).now()
        obj = SmartConnect(api_key=acct["api_key"])
        data = obj.generateSession(acct["client_id"], acct["mpin"], totp)
        if not data or not data.get("status"):
            err_msg = data.get("message") if data else "no response"
            log.error("Account %d login failed: %s", acct['id'], err_msg)
            acct["login_failures"] += 1
            if acct["login_failures"] == 1:
                acct["cooldown_until"] = now + 150
                acct["circuit_error"] = f"First login failed: {err_msg}"
            else:
                acct["circuit_broken"] = True
                acct["cooldown_until"] = now + 900
                acct["circuit_error"] = f"Multiple failures. Circuit broken. Error: {err_msg}"
            return False
            
        acct["login_failures"] = 0
        acct["cooldown_until"] = 0.0
        acct["circuit_broken"] = False
        acct["circuit_error"] = ""
        acct["smart_api"] = obj
        acct["feed_token"] = obj.getfeedToken()
        acct["auth_token"] = data["data"]["jwtToken"]
        acct["last_login"] = time.time()
        log.info("Angel One Account %d login successful", acct['id'])
        return True
    except Exception as exc:
        err_msg = str(exc)
        log.exception("Account %d login exception: %s", acct['id'], exc)
        acct["login_failures"] += 1
        if acct["login_failures"] == 1:
            acct["cooldown_until"] = now + 150
            acct["circuit_error"] = f"Exception: {err_msg}"
        else:
            acct["circuit_broken"] = True
            acct["cooldown_until"] = now + 900
            acct["circuit_error"] = f"Multiple exceptions. Circuit broken. Error: {err_msg}"
        return False

def _login():
    global _smart_api, _feed_token
    success = False
    for acct in _angel_accounts:
        if _login_account(acct):
            success = True
            
    if success:
        acct = get_active_account()
        if acct and acct["smart_api"]:
            _smart_api = acct["smart_api"]
            _feed_token = acct["feed_token"]
            
    return success

def ensure_session():
    with _session_lock:
        if not _angel_accounts:
            return False
        acct = get_active_account()
        if not acct or not acct["smart_api"] or (time.time() - acct["last_login"]) > 6 * 3600:
            return _login()
        return True

def is_market_open():
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 555 <= mins <= 930

def get_live_prices(symbols=None):
    import db
    with _prices_lock:
        if symbols:
            res = {}
            for s in symbols:
                resolved = db.resolve_symbol(s)
                clean = resolved.upper().replace(".NS", "")
                if clean in _live_prices:
                    res[s] = _live_prices[clean].copy()
                    res[s]["symbol"] = s.upper()
            return res
        return {s: d.copy() for s, d in _live_prices.items()}

def get_live_price(symbol):
    import db
    resolved = db.resolve_symbol(symbol)
    clean = resolved.upper().replace(".NS", "")
    with _prices_lock:
        data = _live_prices.get(clean)
        if data:
            tick = data.copy()
            tick["symbol"] = symbol.upper()
            return tick

    # Fallback to yfinance if not in WebSocket cache
    if not yf_is_available():
        log.debug("get_live_price: yf_guard OPEN for %s", clean)
        return None
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{clean}.NS", session=get_yf_session())
        info = ticker.fast_info
        ltp = info.get("lastPrice") or info.get("last_price")
        if ltp:
            tick = {
                "symbol": symbol.upper(),
                "ltp": round(float(ltp), 2),
                "open": round(float(info.get("open", ltp)), 2),
                "high": round(float(info.get("high", ltp)), 2),
                "low": round(float(info.get("low", ltp)), 2),
                "close": round(float(info.get("previousClose", ltp)), 2),
                "change": round(float(info.get("dayPercentChange", 0.0) * ltp / 100), 2),
                "change_pct": round(float(info.get("dayPercentChange", 0.0) * 100), 2),
                "volume": int(info.get("lastVolume", 0)),
                "last_update": datetime.now().isoformat(timespec="seconds"),
            }
            with _prices_lock:
                _live_prices[clean] = tick.copy()
            yf_record_success()
            return tick
    except Exception as exc:
        log.debug("yfinance live fallback failed for %s: %s", clean, exc)
        yf_record_failure()
    return None

def _on_data(wsapp, message):
    try:
        if not isinstance(message, dict):
            return
        token = str(message.get("token", ""))
        symbol = get_symbol(token)
        if not symbol:
            return

        ltp = float(message.get("last_traded_price", 0)) / 100
        close_price = float(message.get("closed_price", 0)) / 100
        open_price = float(message.get("open_price_of_the_day", 0)) / 100
        high_price = float(message.get("high_price_of_the_day", 0)) / 100
        low_price = float(message.get("low_price_of_the_day", 0)) / 100
        volume = int(message.get("volume_trade_for_the_day", 0))

        change = ltp - close_price if close_price > 0 else 0
        change_pct = round((change / close_price) * 100, 2) if close_price > 0 else 0

        tick_time = datetime.now(_IST)

        with _prices_lock:
            _live_prices[symbol] = {
                "symbol": symbol,
                "ltp": round(ltp, 2),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "change": round(change, 2),
                "change_pct": change_pct,
                "volume": volume,
                "last_update": tick_time.isoformat(timespec="seconds"),
            }

        # Release 4: Fire execution engine tick (sub-second SL/Target evaluation)
        if ltp > 0:
            try:
                from execution_engine import on_tick
                on_tick(symbol, round(ltp, 2), tick_time)
            except Exception:
                pass  # Never block WebSocket thread

    except Exception as exc:
        log.debug("Tick parse error: %s", exc)

def _on_open(wsapp):
    log.info("WebSocket connected")
    if _subscribers:
        subscribe(list(_subscribers))

def _on_error(wsapp, error):
    log.warning("WebSocket error: %s", error)

def _on_close(wsapp, close_status_code=None, close_msg=None):
    global _ws_running
    log.info("WebSocket closed: %s %s", close_status_code, close_msg)
    _ws_running = False

def _subscribe_symbols(symbols):
    global _sws
    if not _sws:
        return

    clean_symbols = []
    for sym in symbols:
        s = sym.upper().replace(".NS", "")
        if get_token(s):
            clean_symbols.append(s)

    if not clean_symbols:
        return

    if len(clean_symbols) > MAX_WS_TOKENS_PER_SESSION:
        clean_symbols = clean_symbols[:MAX_WS_TOKENS_PER_SESSION]

    try:
        for i in range(0, len(clean_symbols), MAX_WS_BATCH_SIZE):
            batch_syms = clean_symbols[i:i + MAX_WS_BATCH_SIZE]
            batch_tokens = [get_token(s) for s in batch_syms if get_token(s)]
            if not batch_tokens:
                continue
            token_list = [{"exchangeType": 1, "tokens": batch_tokens}]
            _sws.subscribe(_correlation_id, _WS_MODE, token_list)
        log.info("Subscribed to %d symbols", len(clean_symbols))
    except Exception as exc:
        log.error("Subscribe error: %s", exc)

def subscribe(symbols):
    import db
    global _subscribers
    new_syms = set()
    for s in symbols:
        resolved = db.resolve_symbol(s)
        clean = resolved.upper().replace(".NS", "")
        if clean not in _subscribers and get_token(clean):
            _subscribers.add(clean)
            new_syms.add(clean)
    if _ws_running and new_syms:
        _subscribe_symbols(new_syms)

def start_websocket():
    global _ws_thread, _ws_running, _sws
    if _ws_running:
        return
    if not ensure_session():
        log.error("Cannot start WebSocket: login failed")
        return
    def _run():
        global _sws, _ws_running
        load_token_map()
        _ws_running = True
        while _ws_running:
            try:
                acct = get_active_account()
                _sws = SmartWebSocketV2(acct.get("auth_token", ""), acct["api_key"], acct["client_id"], acct.get("feed_token", ""))
                _sws.on_data = _on_data
                _sws.on_open = _on_open
                _sws.on_error = _on_error
                _sws.on_close = _on_close
                log.info("Starting WebSocket connection...")
                _sws.connect()
            except Exception as exc:
                log.error("WebSocket crashed: %s", exc)
            if _ws_running:
                time.sleep(5)

    _ws_thread = threading.Thread(target=_run, daemon=True)
    _ws_thread.start()
    log.info("WebSocket thread started")

def stop_websocket():
    global _ws_running, _sws
    _ws_running = False
    if _sws:
        try:
            _sws.close_connection()
        except Exception:
            pass

def _rest_gap():
    global _hist_last_call
    with _hist_lock:
        now = time.time()
        # Phase 4: Use dynamic gap that adapts to recent 429 failures
        gap = get_dynamic_rest_gap()
        wait = gap - (now - _hist_last_call)
        if wait > 0:
            time.sleep(wait)
        _hist_last_call = time.time()

def fetch_ltp_bulk(symbols: list[str]) -> dict:
    if not ensure_session():
        return {}
    results = {}
    _reauth_attempted = False  # P0: Only attempt reauth once per batch
    for sym in symbols:
        _rest_gap()
        clean = sym.upper().replace(".NS", "")
        token = get_token(clean)
        if not token:
            continue
        try:
            data = get_smart_api().ltpData("NSE", f"{clean}-EQ", token)

            # P0: AG8001 detection — reauth once per batch, then retry
            if not _reauth_attempted and data and data.get("errorcode") == "AG8001":
                log.warning("[AG8001] Invalid Token in LTP for %s — forcing reauth", clean)
                _reauth_attempted = True
                if force_reauth(reason=f"AG8001_fetch_ltp_{clean}"):
                    _rest_gap()
                    data = get_smart_api().ltpData("NSE", f"{clean}-EQ", token)

            if data.get("status") and data.get("data"):
                d = data["data"]
                ltp = float(d.get("ltp", 0))
                close_price = float(d.get("close", 0))
                change = ltp - close_price if close_price else 0
                change_pct = round((change / close_price) * 100, 2) if close_price else 0
                results[clean] = {
                    "symbol": clean,
                    "ltp": ltp,
                    "open": float(d.get("open", 0)),
                    "high": float(d.get("high", 0)),
                    "low": float(d.get("low", 0)),
                    "close": close_price,
                    "change": round(change, 2),
                    "change_pct": change_pct,
                    "last_update": datetime.now().strftime("%H:%M:%S"),
                }
        except Exception as exc:
            log.debug("LTP fetch failed for %s: %s", clean, exc)
    return results

@timed("fetch_historical")
def fetch_historical(symbol: str, days: int = 365):
    import pandas as pd
    import yfinance as yf

    clean = symbol.upper().replace(".NS", "")
    if not ensure_session():
        log.debug("No Angel session. Using yfinance for %s", clean)
        if not yf_is_available():
            log.debug("fetch_historical: yf_guard OPEN for %s — skipping yfinance", clean)
            return None
        try:
            df = yf.Ticker(f"{clean}.NS", session=get_yf_session()).history(period="1y")
            if df.empty:
                yf_record_failure()
                return None
            df = df.reset_index().rename(columns={
                "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
            })
            df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
            yf_record_success()
            return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
        except Exception:
            yf_record_failure()
            return None

    token = get_token(clean)
    if not token:
        return None

    _rest_gap()
    try:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "ONE_DAY",
            "fromdate": start_dt.strftime("%Y-%m-%d 09:15"),
            "todate": end_dt.strftime("%Y-%m-%d 15:30"),
        }
        result = get_smart_api().getCandleData(params)
        
        # Phase 4, Section 27: Exponential backoff with jitter on rate limit (up to 3 retries)
        for _retry_attempt in range(3):
            if not result or result.get("errorcode") != "AB1019":
                break
            _record_429()
            _backoff = (2 ** _retry_attempt) * 1.5 + random.uniform(0, 0.5)
            log.warning("Angel API 429 (AB1019) for %s — retry %d/3 in %.1fs", clean, _retry_attempt + 1, _backoff)
            time.sleep(_backoff)
            _rest_gap()
            result = get_smart_api().getCandleData(params)

        # P0: AG8001 (Invalid Token) detection — reauth + single retry
        if result and result.get("errorcode") == "AG8001":
            log.warning("[AG8001] Invalid Token for %s — forcing reauth", clean)
            if force_reauth(reason=f"AG8001_fetch_historical_{clean}"):
                _rest_gap()
                result = get_smart_api().getCandleData(params)
            else:
                log.error("[AG8001] Reauth failed for %s — falling back", clean)

        if not result or not result.get("status") or not result.get("data"):
            log.warning("Candle query fail for %s. Trying yfinance fallback...", clean)
            if yf_is_available():
                try:
                    df = yf.Ticker(f"{clean}.NS", session=get_yf_session()).history(period="1y")
                    if not df.empty:
                        df = df.reset_index().rename(columns={
                            "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                            "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                        })
                        df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                        yf_record_success()
                        return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
                except Exception:
                    yf_record_failure()
            return None

        rows = [{
            "DATE": pd.Timestamp(c[0]),
            "OPEN": float(c[1]),
            "HIGH": float(c[2]),
            "LOW": float(c[3]),
            "CLOSE": float(c[4]),
            "VOLUME": int(c[5]),
        } for c in result["data"]]
        df = pd.DataFrame(rows)
        return df if not df.empty else None
    except Exception as exc:
        log.warning("Historical exception for %s: %s. Trying yfinance fallback...", clean, exc)
        if yf_is_available():
            try:
                df = yf.Ticker(f"{clean}.NS", session=get_yf_session()).history(period="1y")
                if not df.empty:
                    df = df.reset_index().rename(columns={
                        "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                        "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                    })
                    df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                    yf_record_success()
                    return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
            except Exception:
                yf_record_failure()
        return None
