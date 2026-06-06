"""API routes — scan, results, live prices, stock detail, export."""

import csv
import io
import json
import time
import hashlib
import logging
import threading
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, request, Response, make_response
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator
from jugaad_data.nse import stock_df
import math


def sanitize_nan(obj):
    if isinstance(obj, dict):
        return {k: sanitize_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_nan(x) for x in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def safe_float(v, default=None):
    if v is None or pd.isna(v):
        return default
    try:
        val = float(v)
        if math.isnan(val) or math.isinf(val):
            return default
        return round(val, 2)
    except Exception:
        return default

from config import TOP_N_RESULTS, DATA_LOOKBACK_DAYS
from stocks import SECTORS
from universe import get_universe_stats
from scanner import scan_state, run_full_scan
from analyzer import fetch_and_analyze, yf_guard_status
from routes.auth import admin_required
from intelligence.fundamentals import extract_detailed_financials, safe_load_json
from metrics.timer import timed
import live_feed
import db
import cache_layer

api_bp = Blueprint("api", __name__)

# ─── Phase 10: Detail Page Indicator Cache ───
DETAIL_CACHE_DIR = Path("cache/detail")
DETAIL_CACHE_TTL = 15 * 60  # 15 minutes


def get_cached_indicator_series(symbol: str) -> dict | None:
    """Load cached indicator series if fresh + scan-valid."""
    path = DETAIL_CACHE_DIR / f"{symbol.upper()}.json"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > DETAIL_CACHE_TTL:
        return None
    data = safe_load_json(path)
    if data is None:
        return None
    # Scan freshness check: if DB has newer data, invalidate
    cached_scan_at = data.get("_last_scan_at", "")
    db_stock = db.get_stock(symbol.upper())
    if db_stock and db_stock.get("updated_at", "") > cached_scan_at:
        return None
    return data


def save_indicator_series(symbol: str, data: dict):
    """Save indicator series to cache."""
    DETAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_last_scan_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = DETAIL_CACHE_DIR / f"{symbol.upper()}.json"
    path.write_text(json.dumps(data, default=str), encoding="utf-8")


@api_bp.route("/api/scan", methods=["POST"])
@admin_required
def start_scan():
    if scan_state.is_scanning:
        return jsonify({"status": "already_scanning"})
    threading.Thread(target=run_full_scan, daemon=True).start()
    return jsonify({"status": "started"})


@api_bp.route("/api/force-scan", methods=["POST"])
@admin_required
def force_scan():
    if scan_state.is_scanning:
        return jsonify({"status": "already_scanning"})
    threading.Thread(target=run_full_scan, daemon=True).start()
    return jsonify({"status": "force_started"})


@api_bp.route("/api/status")
def scan_status():
    def _compute():
        state = scan_state.status()
        return {
            "scanning": state["scanning"],
            "progress": state["progress"],
            "total": state["total"],
            "last_scan": db.get_meta("last_scan"),
            "market_regime": db.get_meta("market_regime", "unknown"),
            "login_status": db.get_meta("angel_login_status", {}),
        }
    return jsonify(cache_layer.get_or_compute(cache_layer.status_cache, "status", _compute))


@api_bp.route("/api/results")
def get_results():
    sort_by = request.args.get("sort", "score")
    order = request.args.get("order", "desc")

    def _compute_results():
        results = db.load_results(TOP_N_RESULTS)
        state = scan_state.status()
        try:
            universe_size = len(STOCK_UNIVERSE)
            if universe_size < 1000:
                universe_size = 2200
        except Exception:
            universe_size = 2200
        return {
            "results": results,
            "total_analyzed": db.get_result_count(),
            "universe_size": universe_size,
            "last_scan": db.get_meta("last_scan"),
            "errors": state.get("errors", 0),
            "nifty50_1m": db.get_meta("nifty50_1m", 0),
            "summary": db.get_meta("summary", ""),
            "heatmap": db.get_meta("heatmap", []),
            "market_regime": db.get_meta("market_regime", "unknown"),
            "login_status": db.get_meta("angel_login_status", {}),
        }

    data = cache_layer.get_or_compute(cache_layer.results_cache, "results", _compute_results)

    # Apply client-requested sorting (on cached data)
    valid_sorts = [
        "score", "price", "rsi", "adx", "volume_ratio", "pct_1w", "pct_1m",
        "delivery_pct", "risk_score", "rs_vs_nifty", "risk_reward", "target_pct",
        "atr_pct", "stoch_k", "bb_position",
    ]
    if sort_by in valid_sorts and sort_by != "score":
        sorted_results = sorted(data["results"], key=lambda x: x.get(sort_by) or 0, reverse=(order == "desc"))
        return jsonify(sanitize_nan({**data, "results": sorted_results}))

    return jsonify(sanitize_nan(data))


@api_bp.route("/api/export/csv")
@admin_required
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "Rank", "Symbol", "Sector", "Score", "High Conviction", "Price",
        "Target", "Target%", "StopLoss(ATR)", "SL%", "R:R",
        "RSI", "ADX", "MACD", "Volume", "Weekly Trend",
        "1W%", "2W%", "1M%", "Delivery%", "Fib Level",
        "Risk Score", "RS vs Nifty", "Breakout", "Accumulation",
        "Support S1", "Resistance R1",
    ]
    writer.writerow(headers)
    for i, r in enumerate(db.load_results(TOP_N_RESULTS)):
        sr = r.get("support_resistance", {})
        writer.writerow([
            i + 1, r["symbol"], r["sector"], r["score"],
            "YES" if r.get("high_conviction") else "",
            r["price"], r["target_price"], r.get("target_pct", ""),
            r["stop_loss"], r.get("stop_loss_pct", ""), r.get("risk_reward", ""),
            r["rsi"], r.get("adx", ""), r["macd_signal"], r["volume_ratio"],
            r.get("weekly_trend", ""),
            r["pct_1w"], r["pct_2w"], r["pct_1m"], r.get("delivery_pct", ""),
            r.get("fib_level", ""), r.get("risk_score", ""), r.get("rs_vs_nifty", ""),
            "YES" if r.get("is_breakout") else "", "YES" if r.get("vp_divergence") else "",
            sr.get("s1", ""), sr.get("r1", ""),
        ])
    output.seek(0)
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=nifty250_v4_{date.today()}.csv"},
    )


@api_bp.route("/api/stock/<symbol>")
@timed("detail_page_response")
def stock_data(symbol):
    """Return extended indicator series for the detail page.
    Phase 10: Uses indicator series cache (15min TTL, scan-invalidated).
    Financials split to /api/stock/<symbol>/financials for async loading.
    """
    clean = symbol.upper().replace(".NS", "")
    cached_db = db.get_stock(clean)

    # Phase 10: Try indicator series cache first
    series_cache = get_cached_indicator_series(clean)
    if series_cache is not None:
        # Warm cache hit — serve directly, add scan data
        result = series_cache.copy()
        result.pop("_last_scan_at", None)
        if cached_db:
            result["scan"] = _build_scan_dict(cached_db)
        # Phase 10: financials loaded async
        result["financials_detailed"] = {
            "loading": True,
            "endpoint": f"/api/stock/{clean}/financials"
        }
        _freshness = time.time() - Path(DETAIL_CACHE_DIR / f"{clean}.json").stat().st_mtime
        result["data_freshness_seconds"] = round(_freshness)
        resp = make_response(jsonify(sanitize_nan(result)))
        resp.headers["Cache-Control"] = "max-age=900"
        return resp

    try:
        # Try Angel One first, fallback to jugaad_data
        df = live_feed.fetch_historical(clean, days=DATA_LOOKBACK_DAYS)
        if df is None or df.empty or len(df) < 50:
            end_date = date.today()
            start_date = end_date - timedelta(days=DATA_LOOKBACK_DAYS)
            df = stock_df(symbol=clean, from_date=start_date, to_date=end_date)

        if df.empty or len(df) < 50:
            return jsonify({"error": "Insufficient data"}), 404

        df = df.sort_values("DATE").reset_index(drop=True)
        close = df["CLOSE"].astype(float)
        high = df["HIGH"].astype(float)
        low = df["LOW"].astype(float)
        volume = df["VOLUME"].astype(float)
        delivery_pct = (df["DELIVERY %"].astype(float)
                        if "DELIVERY %" in df.columns
                        else pd.Series([50.0] * len(df)))

        rsi_series = RSIIndicator(close, window=14).rsi()
        macd_ind = MACD(close)
        macd_line_s = macd_ind.macd()
        macd_sig_s = macd_ind.macd_signal()
        macd_hist_s = macd_ind.macd_diff()
        ema_9_s = EMAIndicator(close, window=9).ema_indicator()
        ema_21_s = EMAIndicator(close, window=21).ema_indicator()
        sma_50_s = SMAIndicator(close, window=50).sma_indicator()
        ema_200_s = EMAIndicator(close, window=min(200, len(close) - 1)).ema_indicator()
        bb = BollingerBands(close, window=20, window_dev=2)
        bb_upper_s = bb.bollinger_hband()
        bb_lower_s = bb.bollinger_lband()
        bb_mid_s = bb.bollinger_mavg()
        stoch = StochasticOscillator(high, low, close)
        stoch_k_s = stoch.stoch()
        stoch_d_s = stoch.stoch_signal()
        adx_s = ADXIndicator(high, low, close, window=14).adx()
        atr_s = AverageTrueRange(high, low, close, window=14).average_true_range()
        obv_s = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
        avg_vol_s = volume.rolling(20).mean()

        def safe_list(series):
            return [safe_float(v) for v in series]

        dates = [
            r["DATE"].strftime("%Y-%m-%d") if hasattr(r["DATE"], "strftime")
            else str(r["DATE"])[:10]
            for _, r in df.iterrows()
        ]

        ohlcv = []
        for _, row in df.iterrows():
            ohlcv.append({
                "date": row["DATE"].strftime("%Y-%m-%d") if hasattr(row["DATE"], "strftime") else str(row["DATE"])[:10],
                "o": safe_float(row.get("OPEN", row["CLOSE"])),
                "h": safe_float(row["HIGH"]),
                "l": safe_float(row["LOW"]),
                "c": safe_float(row["CLOSE"]),
                "v": int(row.get("VOLUME", 0)) if not pd.isna(row.get("VOLUME")) else 0,
            })

        sector = SECTORS.get(clean, "Other")
        current_price = safe_float(close.iloc[-1])

        result = {
            "symbol": clean, "sector": sector,
            "price": current_price,
            "high_52w": safe_float(high.max()),
            "low_52w": safe_float(low.min()),
            "dates": dates, "ohlcv": ohlcv,
            "close": safe_list(close), "volume": [int(v) if not pd.isna(v) else 0 for v in volume],
            "delivery": safe_list(delivery_pct),
            "rsi": safe_list(rsi_series),
            "macd_line": safe_list(macd_line_s), "macd_signal": safe_list(macd_sig_s),
            "macd_hist": safe_list(macd_hist_s),
            "ema_9": safe_list(ema_9_s), "ema_21": safe_list(ema_21_s),
            "sma_50": safe_list(sma_50_s), "ema_200": safe_list(ema_200_s),
            "bb_upper": safe_list(bb_upper_s), "bb_lower": safe_list(bb_lower_s),
            "bb_mid": safe_list(bb_mid_s),
            "stoch_k": safe_list(stoch_k_s), "stoch_d": safe_list(stoch_d_s),
            "adx": safe_list(adx_s), "atr": safe_list(atr_s),
            "obv": safe_list(obv_s), "avg_volume": safe_list(avg_vol_s),
            "market_regime": db.get_meta("market_regime", "unknown"),
            "nifty50_1m": db.get_meta("nifty50_1m", 0),
        }

        # Phase 10: Save to indicator cache
        try:
            save_indicator_series(clean, result.copy())
        except Exception:
            pass

        if cached_db:
            result["scan"] = _build_scan_dict(cached_db)

        # Phase 10: Financials loaded async via separate endpoint
        result["financials_detailed"] = {
            "loading": True,
            "endpoint": f"/api/stock/{clean}/financials"
        }
        result["data_freshness_seconds"] = 0

        resp = make_response(jsonify(sanitize_nan(result)))
        resp.headers["Cache-Control"] = "max-age=900"
        return resp
    except Exception as exc:
        logging.getLogger("screener").warning("Stock detail fetch failed for %s: %s", clean, exc)
        return jsonify({"error": str(exc)}), 500


def _build_scan_dict(cached: dict) -> dict:
    """Build scan sub-dict from cached DB result."""
    return {
        "score": cached["score"], "risk_score": cached["risk_score"],
        "risk_reward": cached["risk_reward"],
        "target_price": cached["target_price"], "target_pct": cached.get("target_pct"),
        "stop_loss": cached["stop_loss"], "stop_loss_pct": cached.get("stop_loss_pct"),
        "signals": cached["signals"],
        "high_conviction": cached.get("high_conviction", False),
        "is_breakout": cached.get("is_breakout", False),
        "weekly_trend": cached.get("weekly_trend", "flat"),
        "below_ema200": cached.get("below_ema200", False),
        "vp_divergence": cached.get("vp_divergence", False),
        "fib_level": cached.get("fib_level"),
        "fib_support": cached.get("fib_support"),
        "fib_resistance": cached.get("fib_resistance"),
        "support_resistance": cached.get("support_resistance", {}),
        "pct_1w": cached.get("pct_1w"), "pct_2w": cached.get("pct_2w"),
        "pct_1m": cached.get("pct_1m"), "rs_vs_nifty": cached.get("rs_vs_nifty"),
        "delivery_pct": cached.get("delivery_pct"),
        "delivery_trend": cached.get("delivery_trend"),
        "bb_position": cached.get("bb_position"),
        "vwap_position": cached.get("vwap_position"),
        "grade": cached.get("grade", "📊 Weak"),
        "trade": cached.get("trade", {}),
        "mtf_trends": cached.get("mtf_trends", {}),
        "mtf_score": cached.get("mtf_score", 0),
        "seasonal": cached.get("seasonal", {}),
        "order_book": cached.get("order_book", {}),
        "sector_rotation": cached.get("sector_rotation", {}),
        "gdelt": cached.get("gdelt", {}),
        "news_sentiment": cached.get("news_sentiment", {}),
        "macro_event": cached.get("macro_event", {}),
        "macro_bias": cached.get("macro_bias", 0),
        "events": cached.get("events", []),
        "fundamentals": cached.get("fundamentals", {}),
        "composite_layer_score": cached.get("composite_layer_score", 0),
        "supports": cached.get("supports", []),
        "resistances": cached.get("resistances", []),
    }


@api_bp.route("/api/stock/<symbol>/financials")
@timed("financial_detail_response")
def stock_financials(symbol):
    """Phase 10: Separate financial endpoint for async loading."""
    clean = symbol.upper().replace(".NS", "")
    try:
        cached_db = db.get_stock(clean)
        recent_news_titles = []
        try:
            rows = db.execute_db("SELECT title FROM news_articles WHERE symbol = ?", (clean,), fetch="all")
            if rows:
                recent_news_titles = [r["title"] for r in rows if r.get("title")]
        except Exception:
            pass

        upcoming_events = cached_db.get("events", []) if cached_db else []
        data = extract_detailed_financials(clean, upcoming_events, recent_news_titles)

        # ETag for client-side caching
        etag = hashlib.md5(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()
        resp = make_response(jsonify(data))
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "max-age=900"
        return resp
    except Exception as exc:
        logging.getLogger("screener").warning("Financials fetch failed for %s: %s", clean, exc)
        return jsonify({
            "yearly": [], "quarterly": [],
            "fin_health_score": 0, "fin_health_verdict": "Stressed",
            "fin_alerts": [f"Error: {str(exc)}"],
            "hindi_explanation": {
                "company_strength": "--", "revenue_status": "--", "profit_status": "--",
                "debt_status": "--", "cash_flow_status": "--", "entry_advice": "--", "suitability": "--"
            }
        }), 500


@api_bp.route("/api/live-prices", methods=["POST"])
def live_prices():
    body = request.get_json(silent=True) or {}
    symbols = body.get("symbols", [])
    if not symbols:
        return jsonify({"error": "No symbols provided"}), 400

    symbols = [s.upper().replace(".NS", "") for s in symbols[:500]]
    live_feed.subscribe(symbols)

    ws_prices = live_feed.get_live_prices(symbols)
    missing = [s for s in symbols if s not in ws_prices]
    if missing:
        rest_prices = live_feed.fetch_ltp_bulk(missing[:20])
        ws_prices.update(rest_prices)

    result = {}
    for sym, data in ws_prices.items():
        price = data.get("ltp", 0)
        if not price:
            continue
        entry = {
            "price": price, "open": data.get("open", 0),
            "high": data.get("high", 0), "low": data.get("low", 0),
            "close": data.get("close", 0), "change": data.get("change", 0),
            "change_pct": data.get("change_pct", 0),
            "volume": data.get("volume", 0), "last_update": data.get("last_update", ""),
        }
        scan_data = db.get_stock(sym)
        if scan_data:
            entry["scan_price"] = scan_data.get("price")
        result[sym] = entry

    return jsonify({
        "prices": result, "source": "angel_one",
        "market_open": live_feed.is_market_open(),
        "ws_connected": live_feed._ws_running,
    })


@api_bp.route("/api/custom-stocks", methods=["GET"])
def get_custom_stocks():
    return jsonify({"stocks": db.get_custom_stocks()})


@api_bp.route("/api/custom-stocks", methods=["POST"])
def add_custom_stock():
    body = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "").upper().replace("NSE:", "").replace(".NS", "").strip()
    if not symbol:
        return jsonify({"error": "Symbol required"}), 400

    db.add_custom_stock(symbol, "NSE", body.get("note", ""))
    try:
        nifty_1m = db.get_meta("nifty50_1m", 0)
        regime = db.get_meta("market_regime", "unknown")
        result = fetch_and_analyze(symbol, nifty_1m, regime, scan_mode="deep")
        if result:
            result["custom"] = True
            db.save_results([result])
            live_feed.subscribe([symbol])
            return jsonify({"status": "ok", "symbol": symbol, "score": result["score"], "scanned": True})
        else:
            return jsonify({"status": "ok", "symbol": symbol, "scanned": False, "message": "Added but no data available"})
    except Exception as exc:
        return jsonify({"status": "ok", "symbol": symbol, "scanned": False, "message": str(exc)})


@api_bp.route("/api/custom-stocks/<symbol>", methods=["DELETE"])
def remove_custom_stock(symbol):
    clean = symbol.upper().replace("NSE:", "").replace(".NS", "")
    return jsonify({"status": "ok", "removed": db.remove_custom_stock(clean)})


def get_git_commit_sha():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("utf-8").strip()
    except Exception:
        return "unknown"

@api_bp.route("/api/health")
def health():
    try:
        db_info = db.db_stats()
    except Exception:
        db_info = {}
    state = scan_state.status()
    try:
        uni = get_universe_stats()
    except Exception:
        uni = {}
    try:
        yf_info = yf_guard_status()
    except Exception:
        yf_info = {}
    from metrics import timer
    # Phase 8: MarketAux queue stats
    try:
        from scanner import get_marketaux_queue_depth, get_marketaux_overflow_count
        mx_depth = get_marketaux_queue_depth()
        mx_overflow = get_marketaux_overflow_count()
    except Exception:
        mx_depth = 0
        mx_overflow = 0
    # Phase 11: Counters
    try:
        from metrics import counters
        app_counters = counters.get_all()
    except Exception:
        app_counters = {}
    try:
        dlq_count = db.dlq_entry_count()
    except Exception:
        dlq_count = 0
    return jsonify({
        "status": "ok",
        "app_version": "v5.0-production",
        "git_commit_sha": get_git_commit_sha(),
        "build_date": "2026-06-05",
        "universe": uni,
        "db_results": db_info.get("results", 0),
        "db_size_kb": db_info.get("db_size_kb", 0),
        "scanning": state["scanning"],
        "market_regime": db.get_meta("market_regime", "unknown"),
        "ws_connected": live_feed._ws_running,
        "live_symbols": len(live_feed._subscribers),
        "perf_timings": timer.get_report(),
        "marketaux_queue_depth": mx_depth,
        "marketaux_overflow_count": mx_overflow,
        "counters": app_counters,
        "dlq_entries": dlq_count,
        **db_info,
        **yf_info,
    })


@api_bp.route("/api/debug/perf-baseline")
@admin_required
def perf_baseline():
    try:
        import json
        baseline_str = db.get_meta("perf_baseline")
        if baseline_str:
            return jsonify(json.loads(baseline_str))
        return jsonify({"error": "No baseline captured yet"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/debug/yf-guard")
@admin_required
def yf_guard_status_endpoint():
    """Return yfinance circuit breaker state (admin only)."""
    try:
        status = yf_guard_status()
        return jsonify({"ok": True, **status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/universe")
def universe_info():
    """Return active scan universe stats and symbol list (read-only)."""
    try:
        import json
        from pathlib import Path
        stats = get_universe_stats()
        # Also return first 100 symbols from cache if available
        active_file = Path(__file__).parent.parent / "cache" / "active_universe.json"
        symbols_preview = []
        if active_file.exists():
            try:
                data = json.loads(active_file.read_text())
                symbols_preview = data.get("symbols", [])[:100]
            except Exception:
                pass
        return jsonify({
            "ok": True,
            **stats,
            "symbols_preview": symbols_preview,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/stock/history/<symbol>")
def stock_score_history(symbol):
    return jsonify({"symbol": symbol.upper(), "history": db.get_score_history(symbol.upper(), days=30)})


# ═══════════════════════════════════════════════════════════════
# INTELLIGENCE API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@api_bp.route("/api/macro")
def get_macro():
    """Returns FRED macro data + world market indices + spot prices."""
    try:
        from intelligence import get_world_snapshot, get_macro_snapshot, scan_world_markets
        world = get_world_snapshot()
        macro = get_macro_snapshot()
        if not world or not macro:
            threading.Thread(target=scan_world_markets, daemon=True).start()
        return jsonify({
            "world": world,
            "macro": macro,
            "regime": db.get_meta("market_regime", "unknown"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "world": {}, "macro": {}})


@api_bp.route("/api/sector-rotation")
def get_sector_rotation():
    """Returns RRG data for all 12 Nifty sector indices."""
    def _compute():
        try:
            from intelligence.sector_rotation import get_rrg_data, scan_sector_rotation
            sectors = get_rrg_data()
            if not sectors:
                threading.Thread(target=scan_sector_rotation, daemon=True).start()
            return {"sectors": sectors}
        except Exception as exc:
            return {"error": str(exc), "sectors": {}}
    return jsonify(cache_layer.get_or_compute(cache_layer.sector_cache, "sector", _compute))


@api_bp.route("/api/seasonal")
def get_seasonal():
    """Returns current month's active seasons and sector boost map."""
    try:
        from datetime import datetime
        import pytz
        from intelligence.seasonal import INDIA_SEASONS, SECTOR_SEASONAL_BOOST
        IST = pytz.timezone("Asia/Kolkata")
        month = datetime.now(IST).month
        active = INDIA_SEASONS.get(month, [])
        boosted = {k: month in months for k, months in SECTOR_SEASONAL_BOOST.items()}
        return jsonify({
            "month": month,
            "active_seasons": active,
            "sector_boosts": {k: v for k, v in boosted.items() if v},
        })
    except Exception as exc:
        return jsonify({"error": str(exc)})


@api_bp.route("/api/macro-events")
def get_macro_events():
    """Returns Forex Factory calendar events and macro regime."""
    try:
        from intelligence.macro_events import get_ff_events, get_ff_regime, scan_macro_events
        events = get_ff_events()
        regime = get_ff_regime()
        if not events:
            threading.Thread(target=scan_macro_events, daemon=True).start()
        return jsonify({
            "events": events,
            "regime": regime,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "events": [], "regime": "NEUTRAL"})


@api_bp.route("/api/news/headlines")
def get_headlines():
    """Returns global macro headlines from NewsAPI (quota-guarded)."""
    try:
        from intelligence.news_sentiment import get_global_headlines
        return jsonify({"headlines": get_global_headlines()})
    except Exception as exc:
        return jsonify({"error": str(exc), "headlines": []})


@api_bp.route("/api/debug/macro-state")
def debug_macro_state():
    """Debug: inspect in-process macro and sector rotation state directly."""
    try:
        import intelligence.macro as _macro
        import intelligence.sector_rotation as _rrg
        return jsonify({
            "world_len": len(_macro.world_snapshot),
            "macro_len": len(_macro.macro_snapshot),
            "built_at": _macro._macro_built_at,
            "scan_running": _macro._scan_running,
            "world_keys": list(_macro.world_snapshot.keys())[:5],
            "macro_keys": list(_macro.macro_snapshot.keys())[:5],
            "rrg_sectors": len(_rrg.sector_rotation_cache),
            "rrg_running": _rrg._rrg_running,
            "rrg_built_at": _rrg._rrg_built_at,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)})


@api_bp.route("/api/stocks")
def get_all_scanned_stocks():
    """Return all scanned stocks."""
    return jsonify({"stocks": db.load_results(TOP_N_RESULTS)})


@api_bp.route("/api/top-candidates")
def get_top_candidates():
    """Return candidates divided into Swing, News-based, Breakouts, Underdogs, and Golden Stocks."""
    results = db.load_results(TOP_N_RESULTS)
    candidates = [r for r in results if (r.get("volume_ratio", 0.0) > 1.5 or r.get("is_breakout", False) or r.get("gdelt", {}).get("spike", 1.0) > 3.0)]
    
    # 1. Golden Candidates (Top 20)
    golden_c = [r for r in results if r.get("is_golden", False)]
    golden = sorted(golden_c, key=lambda x: x.get("score", 0), reverse=True)[:20]

    # 2. Swing Candidates (Top 20)
    swing = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)[:20]
    
    # 3. News Candidates (Top 20)
    news_c = [c for c in candidates if (len(c.get("gdelt", {}).get("articles", [])) > 0 or c.get("news_sentiment_score", 15.0) != 15.0)]
    news = sorted(news_c, key=lambda x: x.get("news_sentiment_score", 0.0), reverse=True)[:20]
    
    # 4. Breakout Candidates (Top 20)
    breakout_c = [c for c in candidates if c.get("is_breakout", False)]
    breakout = sorted(breakout_c, key=lambda x: x.get("score", 0), reverse=True)[:20]
    
    # 5. Underdog Candidates (Top 20)
    underdog_c = []
    for c in candidates:
        mcap = c.get("fundamentals", {}).get("market_cap")
        is_small = mcap is None or mcap < 50000000000  # < 5000 Cr
        is_spiked = c.get("gdelt", {}).get("spike", 1.0) > 3.0 or c.get("volume_ratio", 1.0) > 2.0
        if is_small and is_spiked:
            underdog_c.append(c)
    underdog = sorted(underdog_c, key=lambda x: x.get("score", 0), reverse=True)[:20]
    
    return jsonify({
        "golden": golden,
        "swing": swing,
        "news": news,
        "breakout": breakout,
        "underdog": underdog
    })


@api_bp.route("/api/golden")
def get_golden_list():
    """Return top golden stocks."""
    results = db.load_results(TOP_N_RESULTS)
    golden_c = [r for r in results if r.get("is_golden", False)]
    golden = sorted(golden_c, key=lambda x: x.get("score", 0), reverse=True)[:20]
    return jsonify({"golden": golden})


@api_bp.route("/api/news")
def get_recent_news():
    """Return recent news articles scanned from GDELT/MarketAux."""
    rows = db.execute_db("SELECT symbol, title, url, source, raw_score as score, scanned_at FROM news_articles ORDER BY scanned_at DESC LIMIT 100", fetch="all")
    return jsonify({"news": rows})


@api_bp.route("/api/sentiment")
def get_sentiments():
    """Return recent news sentiment scores."""
    rows = db.execute_db("SELECT symbol, gdelt_sentiment, gdelt_spike, final_sentiment_score as score, updated_at FROM sentiment_scores ORDER BY updated_at DESC LIMIT 100", fetch="all")
    return jsonify({"sentiments": rows})


@api_bp.route("/api/breakouts")
def get_breakouts():
    """Return stocks currently triggering breakout alerts."""
    results = db.load_results(TOP_N_RESULTS)
    breakouts = [r for r in results if r.get("is_breakout", False)]
    return jsonify({"breakouts": breakouts})


@api_bp.route("/api/underdogs")
def get_underdog_list():
    """Return top underdog swing candidate picks."""
    results = db.load_results(TOP_N_RESULTS)
    underdog_c = []
    for c in results:
        mcap = c.get("fundamentals", {}).get("market_cap")
        is_small = mcap is None or mcap < 50000000000
        is_spiked = c.get("gdelt", {}).get("spike", 1.0) > 3.0 or c.get("volume_ratio", 1.0) > 2.0
        if is_small and is_spiked:
            underdog_c.append(c)
    underdog = sorted(underdog_c, key=lambda x: x.get("score", 0), reverse=True)[:20]
    return jsonify({"underdogs": underdog})


@api_bp.route("/api/market-overview")
def get_market_overview():
    """Return macro and global indexes data."""
    try:
        from intelligence import get_world_snapshot, get_macro_snapshot
        from intelligence.macro_events import get_ff_events, get_ff_regime
        return jsonify({
            "world": get_world_snapshot(),
            "macro": get_macro_snapshot(),
            "events": get_ff_events(),
            "regime": get_ff_regime(),
            "nifty50_1m": db.get_meta("nifty50_1m", 0),
            "market_regime": db.get_meta("market_regime", "unknown"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ═══════════════════════════════════════════════════════════════
# RELEASE 3 — PAPER TRADE API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@api_bp.route("/api/paper-trades")
def get_paper_trades():
    """Return all paper trades (open + closed) with live P&L for open positions."""
    try:
        limit = request.args.get("limit", 200, type=int)
        trades = db.get_all_paper_trades(limit)

        # Inject live P&L for open positions from WebSocket cache
        for trade in trades:
            if trade.get("status") == "OPEN":
                live = live_feed.get_live_price(trade.get("symbol", ""))
                if live:
                    entry = trade.get("entry_price", 0)
                    ltp = live.get("ltp", 0)
                    if entry and ltp:
                        trade["current_price"] = ltp
                        trade["live_return_pct"] = round(((ltp - entry) / entry) * 100, 2)
                        qty = trade.get("quantity", 0)
                        trade["live_pnl"] = round((ltp - entry) * qty, 2) if qty else 0
                        trade["day_change_pct"] = live.get("change_pct", 0)

        open_count = sum(1 for t in trades if t.get("status") == "OPEN")
        return jsonify({
            "trades": trades,
            "total": len(trades),
            "open": open_count,
            "closed": len(trades) - open_count,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "trades": []})


@api_bp.route("/api/paper-trades/stats")
def get_paper_trade_stats():
    """Return aggregated paper trading statistics with model version comparison."""
    def _compute():
        try:
            stats = db.get_paper_trade_stats()
            return {"ok": True, **stats}
        except Exception as exc:
            return {"error": str(exc), "ok": False}
    return jsonify(cache_layer.get_or_compute(cache_layer.stats_cache, "stats", _compute))


@api_bp.route("/api/dashboard")
def get_dashboard():
    """Single composite endpoint for the V3 dashboard.

    Returns status + results summary + heatmap + sector + paper stats
    in ONE request instead of 5+. Cached for 10s.
    """
    def _compute():
        # Status
        state = scan_state.status()
        status = {
            "scanning": state["scanning"],
            "progress": state["progress"],
            "total": state["total"],
            "last_scan": db.get_meta("last_scan"),
            "market_regime": db.get_meta("market_regime", "unknown"),
        }

        # Results (pre-sorted by score)
        results = db.load_results(TOP_N_RESULTS)
        total_analyzed = db.get_result_count()

        # Sector rotation
        try:
            from intelligence.sector_rotation import get_rrg_data
            sectors = get_rrg_data() or []
        except Exception:
            sectors = []

        # Paper trade stats
        try:
            paper_stats = db.get_paper_trade_stats()
        except Exception:
            paper_stats = {}

        return {
            "status": status,
            "results": results,
            "total_analyzed": total_analyzed,
            "sector_rotation": {"sectors": sectors},
            "paper_stats": paper_stats,
        }

    return jsonify(cache_layer.get_or_compute(cache_layer.dashboard_cache, "dashboard", _compute))


@api_bp.route("/api/paper-trades/equity-curve")
def get_equity_curve():
    """Return equity curve data for charting."""
    try:
        days = request.args.get("days", 90, type=int)
        curve = db.get_equity_curve(days)
        return jsonify({"curve": curve, "days": days})
    except Exception as exc:
        return jsonify({"error": str(exc), "curve": []})

