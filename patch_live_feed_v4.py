import re

def patch_live_feed():
    with open("live_feed.py", "r", encoding="utf-8") as f:
        content = f.read()

    old_fetch_historical = """@timed("fetch_historical")
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
            df = get_yf_ticker(f"{clean}.NS", source="live_feed_historical").history(period="1y")
            if df.empty:
                yf_record_failure(source="live_feed")
                return None
            df = df.reset_index().rename(columns={
                "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
            })
            df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
            yf_record_success()
            return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
        except Exception:
            yf_record_failure(source="live_feed")
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
                    df = get_yf_ticker(f"{clean}.NS", source="live_feed_historical").history(period="1y")
                    if not df.empty:
                        df = df.reset_index().rename(columns={
                            "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                            "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                        })
                        df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                        yf_record_success()
                        return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
                except Exception:
                    yf_record_failure(source="live_feed")
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
                df = get_yf_ticker(f"{clean}.NS", source="live_feed_historical").history(period="1y")
                if not df.empty:
                    df = df.reset_index().rename(columns={
                        "Date": "DATE", "Open": "OPEN", "High": "HIGH",
                        "Low": "LOW", "Close": "CLOSE", "Volume": "VOLUME"
                    })
                    df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
                    yf_record_success()
                    return df[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
            except Exception:
                yf_record_failure(source="live_feed")
        return None
"""

    new_fetch_historical = """@timed("fetch_historical")
def fetch_historical(symbol: str, days: int = 90):
    import pandas as pd
    from historical_service import get_daily_history

    clean = symbol.upper().replace(".NS", "")
    token = get_token(clean)
    if not token:
        log.warning(f"Could not resolve token for {clean}")
        return None

    try:
        data = get_daily_history(token, days=days, exchange="NSE")
        if not data:
            return None

        rows = [{
            "DATE": pd.Timestamp(c[0]),
            "OPEN": float(c[1]),
            "HIGH": float(c[2]),
            "LOW": float(c[3]),
            "CLOSE": float(c[4]),
            "VOLUME": int(c[5]),
        } for c in data]
        
        df = pd.DataFrame(rows)
        if not df.empty:
            df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
        return df if not df.empty else None
    except Exception as exc:
        log.error("Historical exception for %s via historical_service: %s", clean, exc)
        return None
"""

    # Do exact replacement
    if old_fetch_historical in content:
        content = content.replace(old_fetch_historical, new_fetch_historical)
        with open("live_feed.py", "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched live_feed.py successfully!")
    else:
        print("FAILED to find exact match for fetch_historical in live_feed.py!")

if __name__ == "__main__":
    patch_live_feed()
