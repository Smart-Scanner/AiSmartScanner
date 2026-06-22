"""API routes — scan, results, live prices, stock detail, export."""

import os

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

from metrics import counters

# ── Logger Initialization ──
log = logging.getLogger("api")
log.info("API logger initialized")

# ── Heavy fields that should only load in drawer via /api/stock/<symbol> ──
# These 12 fields account for ~92% of the /api/results payload (3.3MB of 3.6MB)
# but are never rendered in card/list views.
_HEAVY_FIELDS = frozenset({
    "chart_data",           # 1609 KB (50%) — sparkline arrays
    "signals",              #  618 KB (19%) — full signal history
    "fundamentals",         #  231 KB  (7%) — PE/PB/ROE details
    "trade",                #  187 KB  (6%) — entry/exit/SL details
    "order_book",           #   80 KB  (3%) — bid/ask data
    "seasonal",             #   67 KB  (2%) — seasonal patterns
    "news_sentiment",       #   63 KB  (2%) — news scores
    "support_resistance",   #   48 KB  (2%) — S/R levels
    "sector_rotation",      #   28 KB  (1%) — per-stock RRG
    "gdelt",                #   27 KB  (1%) — GDELT articles
    "macro_event",          #   22 KB  (1%) — macro events
    "earnings_signals",     #   19 KB  (1%) — earnings data
})


def _slim_result(stock: dict) -> dict:
    """Return a stock dict stripped of heavy drawer-only fields.

    Keeps all card-essential fields (~65 lightweight fields) like symbol, score,
    price, sector, risk_reward, confidence sub-scores, etc.
    """
    slim = {k: v for k, v in stock.items() if k not in _HEAVY_FIELDS}
    trade = stock.get("trade")
    if isinstance(trade, dict):
        slim["trade_summary"] = {
            "entry_low": trade.get("entry_low"),
            "entry_high": trade.get("entry_high"),
            "stop_loss": trade.get("stop_loss"),
            "target1": trade.get("target1"),
            "target2": trade.get("target2"),
            "target3": trade.get("target3"),
        }
    return slim


def _slim_results(results: list) -> list:
    """Strip heavy fields from a list of stock results."""
    return [_slim_result(r) for r in results]


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

from config import TOP_N_RESULTS, DASHBOARD_MAX_RESULTS, DATA_LOOKBACK_DAYS
from stocks import SECTORS
from universe import get_universe_stats
from scanner import scan_state, run_full_scan
from scan_context import ScanContext
from analyzer import fetch_and_analyze, yf_guard_status
from routes.auth import admin_required
from intelligence.fundamentals import extract_detailed_financials, safe_load_json
from metrics.timer import timed
import live_feed
import db
import cache_layer
from target_utils import resolve_targets

api_bp = Blueprint("api", __name__)

APP_VERSION = os.getenv("APP_VERSION", "v5")

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
    # Phase 0A: Check via DB for active scan (not in-memory singleton)
    active, active_scan_id = db.is_scan_active()
    if active:
        # Section 4: HTTP 409 Conflict with active scan info
        return jsonify({
            "error": "scan_already_active",
            "scan_id": active_scan_id,
            "status": "already_scanning",
        }), 409

    # Phase 1: Create ScanContext at ingress with full attribution
    from flask import session as flask_session
    ctx = ScanContext.create(
        trigger_source="manual",
        user_id=str(flask_session.get("user_id", "unknown")),
        session_id=str(flask_session.get("session_id", "unknown")),
        mode="manual",
    )
    threading.Thread(target=run_full_scan, args=(ctx,), daemon=True).start()
    return jsonify({
        "status": "started",
        "scan_id": ctx.scan_id,
        "correlation_id": ctx.correlation_id,
    })


@api_bp.route("/api/force-scan", methods=["POST"])
@admin_required
def force_scan():
    # Phase 0A: Check via DB for active scan
    active, active_scan_id = db.is_scan_active()
    if active:
        return jsonify({
            "error": "scan_already_active",
            "scan_id": active_scan_id,
            "status": "already_scanning",
        }), 409

    # Phase 1: Create ScanContext at ingress
    from flask import session as flask_session
    ctx = ScanContext.create(
        trigger_source="force",
        user_id=str(flask_session.get("user_id", "unknown")),
        session_id=str(flask_session.get("session_id", "unknown")),
        mode="force",
    )
    threading.Thread(target=run_full_scan, args=(ctx,), daemon=True).start()
    return jsonify({
        "status": "force_started",
        "scan_id": ctx.scan_id,
        "correlation_id": ctx.correlation_id,
    })


@api_bp.route("/api/status")
def scan_status():
    def _compute():
        t0 = time.time()
        state = scan_state.status()

        # Phase C: Single aggregation query replaces 4 separate COUNT queries
        # Uses COALESCE to handle empty tables safely (prevents NULL returns)
        use_pg = db.is_postgresql() and not db.pg_cooldown_active()
        try:
            if use_pg:
                agg = db.execute_db("""
                    SELECT
                        COALESCE(SUM(high_conviction), 0) as hc_count,
                        COALESCE(SUM(CASE WHEN (data->>'is_golden')::text IN ('true','1') THEN 1 ELSE 0 END), 0) as golden_count,
                        COALESCE(SUM(CASE WHEN COALESCE(NULLIF(data->>'change_pct',''),'0')::numeric > 0 OR COALESCE(NULLIF(data->>'price_change_pct',''),'0')::numeric > 0 THEN 1 ELSE 0 END), 0) as adv_count,
                        COALESCE(SUM(CASE WHEN COALESCE(NULLIF(data->>'change_pct',''),'0')::numeric < 0 OR COALESCE(NULLIF(data->>'price_change_pct',''),'0')::numeric < 0 THEN 1 ELSE 0 END), 0) as dec_count
                    FROM scan_results
                """, fetch="one")
            else:
                raise Exception("use sqlite")
        except Exception:
            log.exception("[STATUS PG QUERY FAILED]")
            agg = db.execute_db("""
                SELECT
                    COALESCE(SUM(high_conviction), 0) as hc_count,
                    COALESCE(SUM(CASE WHEN json_extract(data, '$.is_golden') IN (1, 'true') THEN 1 ELSE 0 END), 0) as golden_count,
                    COALESCE(SUM(CASE WHEN CAST(json_extract(data, '$.change_pct') AS REAL) > 0 OR CAST(json_extract(data, '$.price_change_pct') AS REAL) > 0 THEN 1 ELSE 0 END), 0) as adv_count,
                    COALESCE(SUM(CASE WHEN CAST(json_extract(data, '$.change_pct') AS REAL) < 0 OR CAST(json_extract(data, '$.price_change_pct') AS REAL) < 0 THEN 1 ELSE 0 END), 0) as dec_count
                FROM scan_results
            """, fetch="one")

        hc_count = agg.get("hc_count", 0) if isinstance(agg, dict) else 0
        golden_count = agg.get("golden_count", 0) if isinstance(agg, dict) else 0
        adv_count = agg.get("adv_count", 0) if isinstance(agg, dict) else 0
        dec_count = agg.get("dec_count", 0) if isinstance(agg, dict) else 0

        db_time = round((time.time() - t0) * 1000)
        log.info("[STATUS PERF] cache_hit=false | db_time=%dms | query_count=2 | total_time=%dms", db_time, db_time)

        # Phase 5, Section 28: Performance budget check
        if db_time > 100:
            log.warning("[PERFORMANCE] Status endpoint budget exceeded: %d ms (budget: 100ms)", db_time)

        log.info("[STATUS DEBUG] state=%s", state)
        log.info("[STATUS DEBUG] agg=%s type=%s", agg, type(agg))

        return {
            "scanning": state.get("scanning", False),
            "status": state.get("status", "IDLE"),
            "status_source": state.get("status_source", "unknown"),
            "failed_reason": state.get("failed_reason", ""),
            "scan_id": state.get("scan_id", ""),
            "resume_version": state.get("resume_version"),
            "last_attempt": state.get("last_attempt", ""),
            "progress_updated_at": state.get("progress_updated_at", ""),
            "last_successful_scan": state.get("last_successful_scan", ""),
            "is_terminal": state.get("is_terminal", True),
            "progress": state.get("progress", 0),
            "total": state.get("total", 0),
            "last_scan": db.get_meta("last_scan"),
            "market_regime": db.get_meta("market_regime", "unknown"),
            "login_status": db.get_meta("angel_login_status", {}),
            "hc_count": hc_count,
            "golden_count": golden_count,
            "adv_count": adv_count,
            "dec_count": dec_count,
        }

    result = cache_layer.get_status_cache(_compute)

    # Phase 5.5: Inject batch-level progress when universe engine is active
    from config import USE_UNIVERSE_ENGINE
    if USE_UNIVERSE_ENGINE and result.get("scanning"):
        try:
            scan_id = db.get_meta("current_scan_id")
            batch_progress = db.get_batch_progress(scan_id)
            if batch_progress:
                result = dict(result)  # make mutable copy
                result.update({
                    "universe_total": batch_progress.get("universe_total", 0),
                    "completed": batch_progress.get("completed", 0),
                    "remaining": batch_progress.get("remaining", 0),
                    "batch_progress": batch_progress.get("progress", 0),
                    "current_batch": batch_progress.get("current_batch", 0),
                    "total_batches": batch_progress.get("total_batches", 0),
                    "worker_count": 2,
                    "universe_version": batch_progress.get("universe_version", ""),
                })
        except Exception:
            pass

    return jsonify(result)


@api_bp.route("/api/search-list")
def get_search_list():
    t0 = time.time()
    def _compute():
        results = db.get_all_results()
        search_list = []
        for r in results:
            search_list.append({
                "symbol": r.get("symbol"),
                "sector": r.get("sector", ""),
                "score": r.get("score", 0),
                "price": r.get("price", 0.0),
                "high_conviction": bool(r.get("high_conviction", False)),
                "is_golden": bool(r.get("is_golden", False))
            })
        return search_list
    data = cache_layer.get_or_compute(cache_layer.search_cache, "search-list", _compute)
    total_ms = round((time.time() - t0) * 1000)
    log.info("[SEARCH PERF] total_time=%dms", total_ms)
    return jsonify(data)



@api_bp.route("/api/results")
def get_results():
    t_start = time.perf_counter()
    sort_by = request.args.get("sort", "score")
    order = request.args.get("order", "desc")

    timings = {"cache_hit": True, "load_results": 0.0, "status": 0.0, "universe": 0.0, "meta": 0.0}

    def _compute_results():
        timings["cache_hit"] = False
        
        t0 = time.perf_counter()
        results = db.load_results(DASHBOARD_MAX_RESULTS, slim=True)
        timings["load_results"] = round((time.perf_counter() - t0) * 1000, 2)
        
        t0 = time.perf_counter()
        state = scan_state.status()
        timings["status"] = round((time.perf_counter() - t0) * 1000, 2)
        
        t0 = time.perf_counter()
        uni_stats = get_universe_stats()
        universe_size = uni_stats.get("total_symbols", 2200)
        timings["universe"] = round((time.perf_counter() - t0) * 1000, 2)
        
        t0 = time.perf_counter()
        last_scan = db.get_meta("last_scan")
        nifty50_1m = db.get_meta("nifty50_1m", 0)
        summary = db.get_meta("summary", "")
        heatmap = db.get_meta("heatmap", [])
        regime = db.get_meta("market_regime", "unknown")
        login_status = db.get_meta("angel_login_status", {})
        total_analyzed = db.get_result_count()
        timings["meta"] = round((time.perf_counter() - t0) * 1000, 2)
        
        return {
            "results": results,
            "total_analyzed": total_analyzed,
            "universe_size": universe_size,
            "last_scan": last_scan,
            "errors": state.get("errors", 0),
            "nifty50_1m": nifty50_1m,
            "summary": summary,
            "heatmap": heatmap,
            "market_regime": regime,
            "login_status": login_status,
        }

    data = cache_layer.get_or_compute(cache_layer.results_cache, "results", _compute_results)

    t_slim_start = time.perf_counter()
    slim = {**data, "results": _slim_results(data.get("results", []))}
    t_slim_ms = round((time.perf_counter() - t_slim_start) * 1000, 2)

    t_sort_start = time.perf_counter()
    valid_sorts = [
        "score", "price", "rsi", "adx", "volume_ratio", "pct_1w", "pct_1m",
        "delivery_pct", "risk_score", "rs_vs_nifty", "risk_reward", "target_pct",
        "atr_pct", "stoch_k", "bb_position",
    ]
    if sort_by in valid_sorts and sort_by != "score":
        sorted_results = sorted(slim["results"], key=lambda x: x.get(sort_by) or 0, reverse=(order == "desc"))
        res_dict = {**slim, "results": sorted_results}
    else:
        res_dict = dict(slim)

    res_dict["metrics"] = {
        "target_resolved_trade": counters.get("target_resolved_trade"),
        "target_resolved_scan": counters.get("target_resolved_scan"),
        "target_missing": counters.get("target_missing"),
        "signal_compare_match": counters.get("signal_compare_match"),
        "signal_compare_mismatch": counters.get("signal_compare_mismatch"),
    }
    t_sort_ms = round((time.perf_counter() - t_sort_start) * 1000, 2)

    t_serialize_start = time.perf_counter()
    resp = jsonify(sanitize_nan(res_dict))
    t_serialize_ms = round((time.perf_counter() - t_serialize_start) * 1000, 2)

    total_ms = round((time.perf_counter() - t_start) * 1000, 2)

    if not timings["cache_hit"]:
        print(f"[RESULTS PERF] load_results={timings['load_results']} ms | status={timings['status']} ms | universe={timings['universe']} ms | meta={timings['meta']} ms | slim={t_slim_ms} ms | sort={t_sort_ms} ms | serialize={t_serialize_ms} ms | total={total_ms} ms")
        logging.getLogger("screener").info("[RESULTS PERF] load_results=%s ms | status=%s ms | universe=%s ms | meta=%s ms | slim=%s ms | sort=%s ms | serialize=%s ms | total=%s ms", timings['load_results'], timings['status'], timings['universe'], timings['meta'], t_slim_ms, t_sort_ms, t_serialize_ms, total_ms)
        # Phase 5, Section 28: Dashboard performance budget check
        if timings['load_results'] > 150:
            log.warning("[PERFORMANCE] Dashboard load_results budget exceeded: %.1f ms (budget: 150ms)", timings['load_results'])
        if total_ms > 500:
            log.warning("[PERFORMANCE] Dashboard total response budget exceeded: %.1f ms (budget: 500ms)", total_ms)

    return resp


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
    import urllib.parse
    clean = urllib.parse.unquote(symbol).strip().upper().replace(".NS", "")
    cached_db = db.get_stock(clean)

    # Phase 10: Try indicator series cache first
    # P0-3: Inject data_unavailable flag
    from symbol_utils import check_symbol_exists
    _data_unavailable = not check_symbol_exists(clean)

    series_cache = get_cached_indicator_series(clean)
    if series_cache is not None:
        # Warm cache hit — serve directly, add scan data
        result = series_cache.copy()
        result.pop("_last_scan_at", None)
        if cached_db:
            result["scan"] = _build_scan_dict(cached_db)
        # D1-A: Inject normalized targets from single source of truth
        result["targets"] = resolve_targets(cached_db, symbol=clean)
        result["contracts"] = {"targets_contract_version": 2}
        # Phase 10: financials loaded async
        result["financials_detailed"] = {
            "loading": True,
            "endpoint": f"/api/stock/{clean}/financials"
        }
        _freshness = time.time() - Path(DETAIL_CACHE_DIR / f"{clean}.json").stat().st_mtime
        result["data_freshness_seconds"] = round(_freshness)
        result["data_unavailable"] = _data_unavailable
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

        # D1-A: Inject normalized targets from single source of truth
        result["targets"] = resolve_targets(cached_db, symbol=clean)
        result["contracts"] = {"targets_contract_version": 2}

        # Phase 10: Financials loaded async via separate endpoint
        result["financials_detailed"] = {
            "loading": True,
            "endpoint": f"/api/stock/{clean}/financials"
        }
        result["data_freshness_seconds"] = 0
        result["data_unavailable"] = _data_unavailable

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
        # Factor sub-scores for drawer radar chart + confidence calc
        "technical_score": cached.get("technical_score", 0),
        "fundamental_score": cached.get("fundamental_score", 0),
        "earnings_momentum_score": cached.get("earnings_momentum_score", 0),
        "smart_money_score": cached.get("smart_money_score", 0),
        "smart_money_100": cached.get("smart_money_100", 0),
        "sector_rotation_score": cached.get("sector_rotation_score", 0),
        "news_sentiment_score": cached.get("news_sentiment_score", 0),
        "macro_score": cached.get("macro_score", 0),
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
    import urllib.parse
    clean = urllib.parse.unquote(symbol).strip().upper().replace(".NS", "")
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
    # Batch load all scan data in one query instead of N+1 per-symbol calls
    scan_map = db.get_stocks_map(list(ws_prices.keys()))
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
        scan_data = scan_map.get(sym)
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

    # Rate limit: 10s cooldown between custom scans (process-local TTLCache)
    if "last_scan" in cache_layer.custom_scan_limiter:
        return jsonify({"error": "Too fast. Wait 10 seconds between additions."}), 429

    # Cap total custom stocks
    existing = db.get_custom_stocks()
    if len(existing) >= 50:
        return jsonify({"error": "Maximum 50 custom stocks allowed."}), 400

    cache_layer.custom_scan_limiter["last_scan"] = True  # auto-expires in 10s

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
    """Detailed health check and telemetry for Mission Control."""
    try:
        from data_provider import provider_manager
        telemetry = provider_manager.get_telemetry()
    except Exception as e:
        telemetry = {"error": str(e)}

    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "ts": int(time.time()),
        "providers": telemetry
    })


@api_bp.route("/api/debug/health")
@admin_required
def debug_health():
    """Full diagnostics endpoint — admin only."""
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
    try:
        from scanner import get_marketaux_queue_depth, get_marketaux_overflow_count
        mx_depth = get_marketaux_queue_depth()
        mx_overflow = get_marketaux_overflow_count()
    except Exception:
        mx_depth = 0
        mx_overflow = 0
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
        "version": APP_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "build_date": "2026-06-06",
        "ts": int(time.time()),
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
    """Return all scanned stocks (slimmed — heavy fields loaded via /api/stock/<symbol>)."""
    return jsonify({"stocks": db.load_results(DASHBOARD_MAX_RESULTS, slim=True)})


@api_bp.route("/api/top-candidates")
@admin_required
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
    golden = db.load_golden_results(100)
    return jsonify({"golden": golden})


@api_bp.route("/api/high-conviction")
def get_high_conviction():
    """Return top high conviction stocks."""
    hc = db.load_high_conviction_results(100)
    return jsonify({"high_conviction": hc})


@api_bp.route("/api/watchlist/details", methods=["POST"])
def get_watchlist_details():
    """Return stock metadata for a list of watchlist symbols."""
    body = request.get_json(silent=True) or {}
    symbols = body.get("symbols", [])
    if not symbols:
        return jsonify({"stocks": {}})
    stocks_map = db.get_stocks_map(symbols)
    return jsonify({"stocks": stocks_map})


@api_bp.route("/api/news")
def get_recent_news():
    """Return recent news articles. Cached 60s."""
    def _compute():
        return db.execute_db("SELECT symbol, title, url, source, raw_score as score, scanned_at FROM news_articles ORDER BY scanned_at DESC LIMIT 100", fetch="all")
    return jsonify({"news": cache_layer.get_or_compute(cache_layer.news_cache, "news", _compute)})


@api_bp.route("/api/sentiment")
def get_sentiments():
    """Return recent news sentiment scores. Cached 60s."""
    def _compute():
        return db.execute_db("SELECT symbol, gdelt_sentiment, gdelt_spike, final_sentiment_score as score, updated_at FROM sentiment_scores ORDER BY updated_at DESC LIMIT 100", fetch="all")
    return jsonify({"sentiments": cache_layer.get_or_compute(cache_layer.news_cache, "sentiment", _compute)})


@api_bp.route("/api/breakouts")
def get_breakouts():
    """Return stocks currently triggering breakout alerts."""
    breakouts = db.load_breakout_results(100)
    return jsonify({"breakouts": breakouts})



@api_bp.route("/api/underdogs")
@admin_required
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
@admin_required
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
# RELEASE 4 — PAPER TRADE & EXECUTION ENGINE API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@api_bp.route("/api/paper-trades")
def get_paper_trades():
    """Return all paper trades (open + closed) with live P&L and stats."""
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

        # P0-5: Failure Isolation for Stats
        stats = {"ok": False}
        try:
            def _get_stats():
                try:
                    return {"ok": True, **db.get_paper_trade_stats()}
                except Exception as exc:
                    return {"error": str(exc), "ok": False}
            stats = cache_layer.get_or_compute(cache_layer.stats_cache, "stats", _get_stats)
        except Exception as exc:
            import logging
            logging.getLogger("api").exception("[PAPER TRADES API] Failed to fetch stats inline")

        # P0-7: Market & Scan State
        market_open = live_feed.is_market_open()
        scan_active, _ = db.is_scan_active()

        # Release 4: Execution Engine stats
        engine_stats = {}
        try:
            from execution_engine import get_engine_stats
            engine_stats = get_engine_stats()
        except Exception:
            pass

        return jsonify({
            "trades": trades,
            "total": len(trades),
            "open": open_count,
            "closed": len(trades) - open_count,
            "stats": stats,
            "market_open": market_open,
            "scan_running": scan_active,
            "engine": engine_stats,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "trades": []})


@api_bp.route("/api/paper-orders")
def get_paper_orders():
    """Return all paper orders with full lifecycle status."""
    try:
        status_filter = request.args.get("status", None)
        limit = request.args.get("limit", 100, type=int)

        if status_filter:
            orders = db.execute_db(
                "SELECT * FROM paper_orders WHERE status = ? ORDER BY order_created_at DESC LIMIT ?",
                (status_filter.upper(), limit), fetch="all"
            ) or []
        else:
            orders = db.execute_db(
                "SELECT * FROM paper_orders ORDER BY order_created_at DESC LIMIT ?",
                (limit,), fetch="all"
            ) or []

        # Inject live prices for PENDING orders
        for order in orders:
            if order.get("status") == "PENDING":
                live = live_feed.get_live_price(order.get("symbol", ""))
                if live:
                    order["current_price"] = live.get("ltp", 0)

        pending_count = sum(1 for o in orders if o.get("status") == "PENDING")
        filled_count = sum(1 for o in orders if o.get("status") == "FILLED")

        return jsonify({
            "orders": orders,
            "total": len(orders),
            "pending": pending_count,
            "filled": filled_count,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "orders": []})


@api_bp.route("/api/paper-trades/engine-stats")
def get_execution_engine_stats():
    """Return execution engine real-time telemetry."""
    try:
        from execution_engine import get_engine_stats, _pending_orders, _active_positions, _state_lock
        stats = get_engine_stats()
        with _state_lock:
            stats["pending_orders"] = sum(len(v) for v in _pending_orders.values())
            stats["active_positions"] = sum(len(v) for v in _active_positions.values())
            stats["pending_symbols"] = list(_pending_orders.keys())[:20]
            stats["active_symbols"] = list(_active_positions.keys())[:20]
        return jsonify({"ok": True, **stats})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


_dashboard_loaded = False


import functools, traceback
def catch_err(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            with open('logs/dash_err.txt', 'w') as errf:
                errf.write(traceback.format_exc())
            return {'error': str(e)}, 500
    return wrapper

@api_bp.route("/api/dashboard")
@catch_err
def get_dashboard():

    """Single composite endpoint for the V3 dashboard.

    Returns status + results summary + heatmap + sector + paper stats
    in ONE request instead of 5+. Cached for 10s.
    """
    global _dashboard_loaded
    is_first_load = not _dashboard_loaded
    if is_first_load:
        _dashboard_loaded = True
        log.info("[DASHBOARD FIRST LOAD] First API call to /api/dashboard")

    t_start = time.perf_counter()
    timings = {"was_computed": False}

    def _compute():
        timings["was_computed"] = True

        # Status
        t0 = time.perf_counter()
        state = scan_state.status()
        timings["status_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Results (pre-sorted by score)
        t0 = time.perf_counter()
        results = db.load_results(DASHBOARD_MAX_RESULTS, slim=True)
        total_analyzed = db.get_result_count()
        timings["load_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Sector rotation
        t0 = time.perf_counter()
        try:
            from intelligence.sector_rotation import get_rrg_data
            sectors = get_rrg_data() or []
        except Exception:
            sectors = []
        timings["sector_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Paper trade stats
        t0 = time.perf_counter()
        try:
            paper_stats = db.get_paper_trade_stats()
        except Exception:
            paper_stats = {}
        timings["stats_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        status = {
            "scanning": state["scanning"],
            "progress": state["progress"],
            "total": state["total"],
            "last_scan": db.get_meta("last_scan"),
            "market_regime": db.get_meta("market_regime", "unknown"),
        }

        return {
            "status": status,
            "results": _slim_results(results),
            "total_analyzed": total_analyzed,
            "sector_rotation": {"sectors": sectors},
            "paper_stats": paper_stats,
        }

    data = cache_layer.get_or_compute(cache_layer.dashboard_cache, "dashboard", _compute)

    t0 = time.perf_counter()
    resp = jsonify(data)
    serialize_ms = round((time.perf_counter() - t0) * 1000, 2)
    total_ms = round((time.perf_counter() - t_start) * 1000, 2)

    if timings["was_computed"]:
        log.info("[DASHBOARD CACHE MISS] total=%.1fms | status=%.1fms load=%.1fms sector=%.1fms stats=%.1fms jsonify=%.1fms",
                 total_ms, timings.get("status_ms", 0), timings.get("load_ms", 0),
                 timings.get("sector_ms", 0), timings.get("stats_ms", 0), serialize_ms)
    else:
        log.info("[DASHBOARD CACHE HIT] total=%.1fms | jsonify=%.1fms", total_ms, serialize_ms)

    if is_first_load:
        log.info("[DASHBOARD FIRST LOAD COMPLETE] %.1fms", total_ms)

    return resp


@api_bp.route("/api/paper-trades/equity-curve")
def get_equity_curve():
    """Return equity curve data for charting."""
    try:
        days = request.args.get("days", 90, type=int)
        curve = db.get_equity_curve(days)
        return jsonify({"curve": curve, "days": days})
    except Exception as exc:
        return jsonify({"error": str(exc), "curve": []})


# ─── Phase 0: Trust & Observability Endpoints ───

@api_bp.route("/api/score-history/<symbol>")
def score_history(symbol):
    """Return score audit trail for a symbol.
    
    Answers: Why did score change? What components moved? Which data source?
    """
    try:
        limit = request.args.get("limit", 30, type=int)
        rows = db.execute_db("""
            SELECT scan_id, scan_time,
                   technical_score, earnings_momentum_score,
                   fundamental_score, smart_money_score, sector_rotation_score,
                   news_sentiment_score, news_spike_score, macro_score, catalyst_score,
                   final_score, data_source, source_reason,
                   provider_latency_ms, data_staleness_hours, scan_version
            FROM score_audit WHERE symbol=?
            ORDER BY scan_time DESC LIMIT ?
        """, (symbol.upper(), limit), fetch="all")

        # Compute deltas if we have at least 2 scans
        history = rows or []
        delta = None
        if history and len(history) >= 2:
            latest = history[0]
            prev = history[1]
            component_keys = [
                "technical_score", "earnings_momentum_score", "fundamental_score",
                "smart_money_score", "sector_rotation_score", "news_sentiment_score",
                "news_spike_score", "macro_score", "catalyst_score"
            ]
            delta = {
                "final_score_change": round((latest.get("final_score") or 0) - (prev.get("final_score") or 0), 2),
                "components": {
                    k: round((latest.get(k) or 0) - (prev.get(k) or 0), 2)
                    for k in component_keys
                },
                "source_changed": latest.get("data_source") != prev.get("data_source"),
                "version_changed": latest.get("scan_version") != prev.get("scan_version"),
            }

        return jsonify({
            "symbol": symbol.upper(),
            "history": history,
            "delta": delta,
            "count": len(history),
        })
    except Exception as exc:
        return jsonify({"symbol": symbol.upper(), "history": [], "delta": None, "error": str(exc)})

@api_bp.route("/api/research-history/<symbol>")
def api_research_history(symbol):
    """Retrieve the full timeline of research snapshots for a symbol."""
    try:
        history = db.get_research_history(symbol.upper())
        return jsonify({
            "symbol": symbol.upper(),
            "history": history,
            "count": len(history)
        })
    except Exception as exc:
        return jsonify({"symbol": symbol.upper(), "history": [], "error": str(exc)})

@api_bp.route("/api/research-advisories", methods=["GET", "POST"])
def api_research_advisories():
    """Get active advisories or create a new one."""
    if request.method == "POST":
        data = request.json or {}
        symbol = data.get("symbol")
        adv_type = data.get("advisory_type")
        adv_text = data.get("advisory_text")
        priority = data.get("priority", "MEDIUM")
        
        if not symbol or not adv_type or not adv_text:
            return jsonify({"error": "Missing required fields"}), 400
            
        try:
            adv_id = db.create_research_advisory(symbol.upper(), adv_type, adv_text, priority)
            return jsonify({"success": True, "advisory_id": adv_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
    else:
        symbol = request.args.get("symbol")
        try:
            advisories = db.get_research_advisories(symbol=symbol.upper() if symbol else None)
            return jsonify({"advisories": advisories})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/health/scan")
def scan_health():
    """Operational health of the scanner.
    
    Returns last scan time, age, data source, scan version, market regime, and status.
    """
    try:
        from config import SCAN_VERSION
    except ImportError:
        SCAN_VERSION = "unknown"

    last_scan = db.get_meta("last_scan")
    scan_age = None
    if last_scan:
        from datetime import datetime as dt
        try:
            last_dt = dt.strptime(last_scan, "%Y-%m-%d %H:%M IST")
            scan_age = round((dt.now() - last_dt).total_seconds() / 60, 1)
        except Exception:
            pass

    # Get latest scan_audit for extra context
    latest_audit = None
    try:
        latest_audit = db.execute_db(
            "SELECT scan_id, duration_ms, stocks_scanned, stocks_succeeded, stocks_failed, data_source, scan_version "
            "FROM scan_audit ORDER BY start_time DESC LIMIT 1",
            fetch="one"
        )
    except Exception:
        pass

    return jsonify({
        "last_scan": last_scan,
        "scan_age_minutes": scan_age,
        "scan_version": SCAN_VERSION,
        "market_regime": db.get_meta("market_regime", "unknown"),
        "status": "healthy" if scan_age and scan_age < 120 else "stale",
        "latest_audit": latest_audit,
    })
