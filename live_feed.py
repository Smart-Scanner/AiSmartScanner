"""
Angel One SmartAPI WebSocket Live Feed
Real-time tick data for Smart Screener
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

import pyotp
import requests
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

log = logging.getLogger("live_feed")

ENV_FILE = Path(__file__).parent / ".env"
TOKEN_FILE = Path(__file__).parent / "cache" / "angel_tokens.json"

API_KEY = ""
CLIENT_ID = ""
MPIN = ""
TOTP_SECRET = ""

_token_map = {}
_reverse_map = {}

_smart_api = None
_auth_token = None
_feed_token = None
_session_lock = threading.Lock()
_last_login = 0

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

REST_GAP_SECONDS = 0.4
_hist_lock = threading.Lock()
_hist_last_call = 0.0

_IST = timezone(timedelta(hours=5, minutes=30))

def _load_env():
    global API_KEY, CLIENT_ID, MPIN, TOTP_SECRET
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    API_KEY = os.environ.get("ANGEL_API_KEY", "")
    CLIENT_ID = os.environ.get("ANGEL_CLIENT_ID", "")
    MPIN = os.environ.get("ANGEL_MPIN", "")
    TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")

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
    return _token_map.get(symbol.upper().replace(".NS", ""))

def get_symbol(token: str):
    return _reverse_map.get(str(token))

def _login():
    global _smart_api, _auth_token, _feed_token, _last_login
    if not all([API_KEY, CLIENT_ID, MPIN, TOTP_SECRET]):
        log.error("Angel One credentials not configured")
        return False
    try:
        totp = pyotp.TOTP(TOTP_SECRET).now()
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, MPIN, totp)
        if not data or not data.get("status"):
            log.error("Login failed: %s", data.get("message") if data else "no response")
            return False
        _smart_api = obj
        _auth_token = data["data"]["jwtToken"]
        _feed_token = obj.getfeedToken()
        _last_login = time.time()
        log.info("Angel One login successful")
        return True
    except Exception as exc:
        log.exception("Login error: %s", exc)
        return False

def ensure_session():
    with _session_lock:
        if _smart_api is None or (time.time() - _last_login) > 6 * 3600:
            return _login()
        return True

def is_market_open():
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 555 <= mins <= 930

def get_live_prices(symbols=None):
    with _prices_lock:
        if symbols:
            return {s: _live_prices[s].copy() for s in symbols if s in _live_prices}
        return {s: d.copy() for s, d in _live_prices.items()}

def get_live_price(symbol):
    clean = symbol.upper().replace(".NS", "")
    with _prices_lock:
        data = _live_prices.get(clean)
        if data:
            return data.copy()

    # Fallback to yfinance if not in WebSocket cache
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{clean}.NS")
        info = ticker.fast_info
        ltp = info.get("lastPrice") or info.get("last_price")
        if ltp:
            tick = {
                "symbol": clean,
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
                _live_prices[clean] = tick
            return tick.copy()
    except Exception as exc:
        log.debug("yfinance live fallback failed for %s: %s", clean, exc)
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
                "last_update": datetime.now().isoformat(timespec="seconds"),
            }
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
    global _subscribers
    new_syms = set()
    for s in symbols:
        clean = s.upper().replace(".NS", "")
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
    load_token_map()

    def _run():
        global _sws, _ws_running
        _ws_running = True
        while _ws_running:
            try:
                _sws = SmartWebSocketV2(_auth_token, API_KEY, CLIENT_ID, _feed_token)
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
        wait = REST_GAP_SECONDS - (now - _hist_last_call)
        if wait > 0:
            time.sleep(wait)
        _hist_last_call = time.time()

def fetch_ltp_bulk(symbols: list[str]) -> dict:
    if not ensure_session():
        return {}
    results = {}
    for sym in symbols:
        _rest_gap()
        clean = sym.upper().replace(".NS", "")
        token = get_token(clean)
        if not token:
            continue
        try:
            data = _smart_api.ltpData("NSE", f"{clean}-EQ", token)
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

def fetch_historical(symbol: str, days: int = 365):
    import pandas as pd
    import yfinance as yf

    clean = symbol.upper().replace(".NS", "")
    if not ensure_session():
        log.debug("No Angel session. Using yfinance for %s", clean)
        try:
            df = yf.Ticker(f"{clean}.NS").history(period="1y")
            if df.empty:
                return None
            df = df.reset_index().rename(columns={
                "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
            })
            df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
            return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
        except Exception:
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
        result = _smart_api.getCandleData(params)
        
        # Retry once on rate limit
        if result and result.get("errorcode") == "AB1019":
            time.sleep(1.5)
            _rest_gap()
            result = _smart_api.getCandleData(params)

        if not result or not result.get("status") or not result.get("data"):
            log.warning("Candle query fail for %s. Trying yfinance fallback...", clean)
            try:
                df = yf.Ticker(f"{clean}.NS").history(period="1y")
                if not df.empty:
                    df = df.reset_index().rename(columns={
                        "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                        "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                    })
                    df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                    return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
            except Exception:
                pass
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
        try:
            df = yf.Ticker(f"{clean}.NS").history(period="1y")
            if not df.empty:
                df = df.reset_index().rename(columns={
                    "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                    "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                })
                df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
        except Exception:
            pass
        return None
