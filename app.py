#!/usr/bin/env python3
"""
Smart Screener — Entry Point
NSE Stock Screener + Portfolio Manager with Angel One Live Feed
"""

import os
import time
import signal
import logging
import threading

from dotenv import load_dotenv
load_dotenv()  # must run before any config import that reads env vars

# Set Windows Process Priority to BELOW_NORMAL to prevent CPU starvation/laptop freezes
import sys
if sys.platform == "win32":
    try:
        import ctypes
        # 0x00004000 = BELOW_NORMAL_PRIORITY_CLASS
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("screener").info("System: Windows process priority set to BELOW_NORMAL to optimize responsiveness.")
    except Exception:
        pass

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_compress import Compress

# Pre-create jugaad_data cache dirs to avoid race condition
for d in [os.path.expanduser("~/.cache/nsehistory-stock"),
          os.path.expanduser("~/.cache/nsehistory-index")]:
    os.makedirs(d, exist_ok=True)

import db
import auth_db
import live_feed
import cache_layer
from config import AUTO_SCAN_INTERVAL, FLASK_SECRET_KEY, DATA_LOOKBACK_DAYS
from scanner import scan_state, has_valid_cache, run_full_scan, _shutdown_event
from scan_context import ScanContext
from analyzer import fetch_and_analyze
from routes.pages import pages_bp
from routes.api import api_bp
from routes.portfolio import portfolio_bp
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.broker_zerodha import zerodha_bp

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__)
# Honor X-Forwarded-* headers so OAuth callbacks built with url_for(_external=True)
# use the public HTTPS scheme/host (ngrok or any reverse proxy) instead of the
# local HTTP origin. Without this, Google rejects with redirect_uri_mismatch.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = FLASK_SECRET_KEY or "nse-screener-dev-key-change-me"
if not FLASK_SECRET_KEY:
    log = logging.getLogger("screener")
    log.warning("FLASK_SECRET_KEY not set — using insecure dev key. Set it in .env before deploy.")

# P3: Gzip/Brotli compression — shrinks API payloads ~80%
Compress(app)

# P4: Browser caches static assets (CSS/JS/fonts) for 24 hours
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(api_bp)

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    with open('logs/flask_err.txt', 'w') as errf:
        errf.write(traceback.format_exc())
    return str(e), 500

app.register_blueprint(portfolio_bp)
app.register_blueprint(zerodha_bp)


@app.context_processor
def inject_template_globals():
    """Variables auto-available in every Jinja template."""
    from datetime import datetime
    return {"current_year": datetime.now().year}

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
log.info("Smart Screener v5 | Stock Screener + Portfolio Manager")

# Init DBs
db.init_db()
auth_db.init_db()

# Phase 5.5: Startup resume + universe rebuild check
from config import USE_UNIVERSE_ENGINE, AUTO_SCAN_ENABLED_DEFAULT
if USE_UNIVERSE_ENGINE:
    log.info("[Phase 5.5] Universe Engine ACTIVE")
    
    # Background Boot Sequence (Master Sync -> Liquidity Worker -> Universe Build)
    def _boot_universe_prep():
        # One-time data repair: fix symbols corrupted by old Phase 1
        # (last_synced_at was set but market_cap=0, meaning yfinance never ran)
        try:
            repaired = db.execute_db(
                """UPDATE universe_catalog SET last_synced_at = NULL
                   WHERE market_cap = 0 AND last_synced_at IS NOT NULL""",
                fetch="rowcount"
            )
            if repaired and repaired > 0:
                log.info("[BootPrep] Data repair: reset last_synced_at for %d unenriched symbols", repaired)
        except Exception as e:
            log.warning("[BootPrep] Data repair error (non-fatal): %s", e)

        log.info("[BootPrep] Running Master Sync...")
        try:
            from master_sync import run_master_sync
            run_master_sync()
        except Exception as e:
            log.error("[BootPrep] Master Sync error: %s", e)

        # Phase 5.6B/C: Launch background liquidity worker (detached from boot)
        # Worker will: freeze candidates → enrich → check coverage → build universe
        log.info("[BootPrep] Launching Background Liquidity Worker...")
        try:
            from liquidity_enrichment import start_background_liquidity_worker
            start_background_liquidity_worker()
        except Exception as e:
            log.error("[BootPrep] Liquidity Worker launch error: %s", e)
            # Fallback: try legacy universe build if worker fails
            log.info("[BootPrep] Falling back to legacy Universe Build...")
            try:
                from universe_builder import build_eligible_universe
                build_eligible_universe()
            except Exception as e2:
                log.error("[BootPrep] Legacy Universe Build error: %s", e2)

        log.info("[BootPrep] Universe Prep Complete.")
        
    threading.Thread(target=_boot_universe_prep, daemon=True, name="boot-prep").start()

    # Check for incomplete scan from Railway restart
    _resume = db.get_pending_resume()
    if _resume and _resume.get("status") == "running":
        log.info("[Phase 5.5] Found incomplete scan %s — scheduling resume",
                 _resume.get("scan_id", "unknown"))
        _resume_ctx = ScanContext.create(trigger_source="resume", user_id="system", mode="auto")
        threading.Thread(target=run_full_scan, args=(_resume_ctx,), daemon=True,
                         name="scan-resume").start()
    else:
        log.info("[Phase 5.5] No pending resume state")

# Start Angel One WebSocket for live prices
try:
    live_feed.start_websocket()
    log.info("Angel One WebSocket started")
except Exception as exc:
    log.warning("WebSocket start failed (will use REST fallback): %s", exc)

# Load cached data or start fresh scan
if has_valid_cache():
    log.info("DB has valid cache (%d stocks). Subscribing to live feed...", db.get_result_count())
    cached_syms = db.get_all_symbols()
    if cached_syms:
        live_feed.subscribe(cached_syms)
        log.info("Subscribed %d cached stocks to live feed", len(cached_syms))
    
    # Warm up global intelligence snapshots (RRG, macro, FRED, GDELT) on startup in background
    from intelligence import warmup_all
    threading.Thread(target=lambda: warmup_all(set(cached_syms) if cached_syms else None), daemon=True, name="startup-warmup").start()
else:
    log.info("No valid cache. Checking auto_scan_enabled toggle before starting first scan...")
    _enabled = db.get_meta("auto_scan_enabled")
    _is_enabled = (_enabled == "1") if _enabled else AUTO_SCAN_ENABLED_DEFAULT
    
    if _is_enabled:
        # Phase 1: Create ScanContext for initial scan
        _startup_ctx = ScanContext.create(trigger_source="auto", user_id="system", mode="auto")
        threading.Thread(target=run_full_scan, args=(_startup_ctx,), daemon=True).start()
    else:
        log.info("AUTO_SCAN_ENABLED is disabled. Waiting for manual scan start or toggle via Mission Control.")


# ---------------------------------------------------------------------------
# Auto-scan scheduler (Phase 4: Event-driven)
# ---------------------------------------------------------------------------

# Interval constants (seconds)
_NEWS_INTERVAL   = 15 * 60     # News refresh every 15 min
_FAST_INTERVAL   = AUTO_SCAN_INTERVAL * 60  # Fast scan (from config, default 60 min)
_DEEP_INTERVAL   = 120 * 60    # Deep scan every 2 hours
_MACRO_INTERVAL  = 60 * 60     # Macro refresh every 1 hour
_GRACE_PERIOD    = 5 * 60      # Skip if manual scan ran < 5 min ago

def _auto_scan_loop():
    """
    Event-driven auto-scan loop (Phase 4).
    Order: News refresh -> Fast scan -> Macro refresh -> Deep scan (if needed).
    Phase 5.5: Also handles master sync + daily universe rebuild.
    """
    from scanner import refresh_news_pipeline, _shortlist_for_deep_scan
    from intelligence import warmup_all

    time.sleep(60)  # startup grace

    # Load timestamps from DB
    def _get_ts(key, default=0.0):
        v = db.get_meta(key)
        try:
            return float(v) if v else default
        except (ValueError, TypeError):
            return default

    last_news  = _get_ts("last_news_refresh_ts")
    last_fast  = _get_ts("last_fast_scan_ts")
    last_deep  = _get_ts("last_deep_scan_ts")
    last_macro = _get_ts("last_macro_refresh_ts")
    last_universe_rebuild = _get_ts("last_universe_rebuild_ts")

    while True:
        try:
            now = time.time()

            # Grace period: skip if manual scan ran < 5 min ago
            last_any = _get_ts("last_scan_ts")
            if last_any and (now - last_any) < _GRACE_PERIOD:
                log.debug("[AutoScan] Grace period active, sleeping")
                time.sleep(30)
                continue

            # Phase 5.5: Master Sync (every 14 days)
            if USE_UNIVERSE_ENGINE:
                try:
                    from master_sync import is_master_sync_due, run_master_sync
                    if is_master_sync_due():
                        log.info("[Phase 5.5] Master sync due — starting")
                        run_master_sync()
                except Exception as exc:
                    log.warning("[Phase 5.5] Master sync failed: %s", exc)

                # Phase 5.6B/C: Daily universe rebuild + liquidity refresh at 8:30 AM IST
                try:
                    from datetime import datetime, timezone, timedelta as _td
                    _IST = timezone(_td(hours=5, minutes=30))
                    _now_ist = datetime.now(_IST)
                    from config import UNIVERSE_REBUILD_HOUR, UNIVERSE_REBUILD_MINUTE
                    if (_now_ist.hour == UNIVERSE_REBUILD_HOUR and
                        _now_ist.minute >= UNIVERSE_REBUILD_MINUTE and
                        _now_ist.minute < UNIVERSE_REBUILD_MINUTE + 5 and
                        (now - last_universe_rebuild) > 3600):
                        log.info("[Phase 5.6B/C] Daily universe refresh triggered")
                        # Kick off liquidity worker which handles freeze → enrich → build
                        from liquidity_enrichment import start_background_liquidity_worker
                        start_background_liquidity_worker()
                        last_universe_rebuild = time.time()
                        db.set_meta("last_universe_rebuild_ts", str(last_universe_rebuild))
                except Exception as exc:
                    log.warning("[Phase 5.6B/C] Daily universe refresh failed: %s", exc)

            market_open = live_feed.is_market_open()

            # 1. NEWS REFRESH — first in market hours
            if market_open and (now - last_news >= _NEWS_INTERVAL):
                log.info("[AutoScan] News refresh starting...")
                try:
                    universe = set(db.get_all_symbols() or [])
                    event_signals = refresh_news_pipeline(universe)
                    last_news = time.time()
                    db.set_meta("last_news_refresh_ts", str(last_news))
                    log.info("[AutoScan] News refresh done")
                except Exception as exc:
                    log.warning("[AutoScan] News refresh error: %s", exc)
                    event_signals = {"spikes": set(), "announcements": set()}
            else:
                event_signals = {"spikes": set(), "announcements": set()}

            # 2. FAST SCAN — second in market hours
            needs_deep = False
            _enabled = db.get_meta("auto_scan_enabled")
            _is_enabled = (_enabled == "1") if _enabled else AUTO_SCAN_ENABLED_DEFAULT

            if market_open and (now - last_fast >= _FAST_INTERVAL) and not scan_state.is_scanning:
                if _is_enabled:
                    log.info("[AutoScan] Market open -- starting fast scan")
                    # Phase 1: Create ScanContext for auto-scan
                    _auto_ctx = ScanContext.create(
                        trigger_source="auto", user_id="system", mode="auto",
                    )
                    run_full_scan(_auto_ctx)
                    last_fast = time.time()
                    db.set_meta("last_fast_scan_ts", str(last_fast))
                    db.set_meta("last_scan_ts", str(last_fast))
                    needs_deep = True
                else:
                    log.info("[AutoScan] Fast scan scheduled but AUTO_SCAN_ENABLED is 0. Skipping.")
            elif not market_open:
                last = db.get_meta("last_scan")
                if not last:
                    if _is_enabled:
                        log.info("[AutoScan] No data yet -- starting scan")
                        _auto_ctx = ScanContext.create(
                            trigger_source="auto", user_id="system", mode="auto",
                        )
                        run_full_scan(_auto_ctx)
                        last_fast = time.time()
                        db.set_meta("last_fast_scan_ts", str(last_fast))
                        db.set_meta("last_scan_ts", str(last_fast))
                    else:
                        log.info("[AutoScan] Initial scan pending but AUTO_SCAN_ENABLED is 0. Skipping.")

            # 3. MACRO REFRESH — any time
            if now - last_macro >= _MACRO_INTERVAL:
                log.info("[AutoScan] Macro refresh...")
                try:
                    from intelligence.macro import scan_world_markets
                    from intelligence.macro_events import scan_macro_events
                    scan_world_markets()
                    scan_macro_events()
                    last_macro = time.time()
                    db.set_meta("last_macro_refresh_ts", str(last_macro))
                except Exception as exc:
                    log.warning("[AutoScan] Macro refresh error: %s", exc)

            # 4. DEEP SCAN — if event signals or interval exceeded
            has_events = bool(event_signals.get("spikes") or event_signals.get("announcements"))
            if (needs_deep or has_events or (now - last_deep >= _DEEP_INTERVAL)) and not scan_state.is_scanning:
                # Deep scan is only for shortlisted candidates, not a full re-scan
                try:
                    all_results = db.get_all_results()  # current fast scan results from DB
                    if all_results:
                        shortlist = _shortlist_for_deep_scan(all_results, event_signals)
                        if shortlist:
                            log.info("[AutoScan] Deep scan for %d shortlisted candidates", len(shortlist))
                            nifty_1m = db.get_meta("nifty50_1m", 0)
                            regime = db.get_meta("market_regime", "unknown")
                            deep_results = []
                            for sym in shortlist:
                                try:
                                    df = live_feed.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
                                    if df is not None and not df.empty:
                                        r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df, scan_mode="deep")
                                        if r:
                                            deep_results.append(r)
                                except Exception:
                                    pass
                            if deep_results:
                                db.save_results(deep_results)
                                log.info("[AutoScan] Deep scan complete: %d stocks enriched", len(deep_results))
                        last_deep = time.time()
                        db.set_meta("last_deep_scan_ts", str(last_deep))
                except Exception as exc:
                    log.warning("[AutoScan] Deep scan error: %s", exc)

        except Exception as exc:
            log.warning("[AutoScan] Error: %s", exc)

        time.sleep(30)  # check every 30 seconds


def _portfolio_scan_loop():
    time.sleep(120)  # wait 2 mins for startup
    while True:
        try:
            log.info("[PortfolioScan] Running 30-min portfolio check...")
            positions = db.execute_db("SELECT id, symbol, buy_price, stop_loss, target FROM positions WHERE status = 'OPEN'", fetch="all")
            if positions:
                symbols = list(set(p["symbol"] for p in positions))
                # Use WebSocket cache instead of rate-limited REST bulk fetch
                prices = {}
                for s in symbols:
                    p_data = live_feed.get_live_price(s)
                    if p_data:
                        prices[s] = p_data
                scan_lookup = db.get_stocks_map(symbols)
                from datetime import datetime
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                for pos in positions:
                    sym = pos["symbol"]
                    buy_price = pos["buy_price"]
                    sl = pos["stop_loss"]
                    tgt = pos["target"]
                    pos_id = pos["id"]

                    # Fallback to scanner values if position-specific values are not set
                    scan = scan_lookup.get(sym, {})
                    if (sl is None or sl == 0) and scan:
                        sl = scan.get("stop_loss")
                    if (tgt is None or tgt == 0) and scan:
                        tgt = scan.get("target_price")

                    price_data = prices.get(sym)
                    if not price_data:
                        price_data = live_feed.get_live_price(sym)

                    if price_data:
                        ltp = price_data.get("ltp") or price_data.get("price", 0.0)
                        if not ltp:
                            continue

                        # Core hold, sell, book scenarios
                        if tgt is not None and ltp >= tgt:
                            rec = "Book Profit (Target Reached)"
                        elif sl is not None and ltp <= sl:
                            rec = "Exit / Stop Loss Triggered"
                        elif ltp > buy_price * 1.05:
                            rec = f"Hold (Trail SL to Cost: ₹{buy_price})"
                        else:
                            rec = "Hold (Position Active)"

                        db.execute_db("UPDATE positions SET scan_analysis = ?, last_scan_at = ? WHERE id = ?", (rec, now_str, pos_id))
                        log.info("[PortfolioScan] Checked %s: LTP=%s, Rec=%s", sym, ltp, rec)
            else:
                log.info("[PortfolioScan] No open positions to scan")
        except Exception as exc:
            log.warning("[PortfolioScan] Error in portfolio scan: %s", exc)
        time.sleep(1800)  # every 30 mins


# ---------------------------------------------------------------------------
# Release 3: Paper Trade Snapshot (11:00 AM IST daily)
# ---------------------------------------------------------------------------
_SNAPSHOT_HOUR = 11  # 11:00 AM IST
_SNAPSHOT_MINUTE = 0

def _paper_trade_snapshot_loop():
    """
    Daily at 11:00 AM IST:
    1. Save top 20 recommendation snapshot
    2. Open paper trades for top 5 picks
    """
    time.sleep(180)  # 3 min startup grace

    while True:
        try:
            from datetime import datetime as _dt
            now = _dt.now()

            # Only trigger at 11:00 AM (±5 min window)
            if now.hour == _SNAPSHOT_HOUR and now.minute < 10:
                today = now.strftime("%Y-%m-%d")

                # Check if already snapped today
                existing = db.execute_db(
                    "SELECT COUNT(*) as cnt FROM recommendation_snapshots WHERE snapshot_date = ?",
                    (today,), fetch="one"
                )
                if existing and existing.get("cnt", 0) > 0:
                    log.debug("[PaperTrade] Snapshot already taken today, skipping")
                    time.sleep(600)  # sleep 10 min to avoid re-trigger
                    continue

                # Get current scan results sorted by score
                all_results = db.get_all_results()
                if not all_results:
                    log.info("[PaperTrade] No scan results available for snapshot")
                    time.sleep(600)
                    continue

                # Sort by score descending
                all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

                # Get market context
                regime = db.get_meta("market_regime", "unknown")
                nifty_price = None
                try:
                    nifty_meta = db.get_meta("nifty50_price")
                    if nifty_meta:
                        nifty_price = float(nifty_meta)
                except Exception:
                    pass

                # 1. Save top 20 recommendation snapshot
                db.save_recommendation_snapshot(today, all_results, regime)

                # 2. Compute market breadth from scan results
                advances = sum(1 for s in all_results if (s.get("change_pct") or s.get("price_change_pct") or 0) > 0)
                declines = sum(1 for s in all_results if (s.get("change_pct") or s.get("price_change_pct") or 0) < 0)
                breadth_ratio = round(advances / declines, 2) if declines > 0 else (9.99 if advances > 0 else 1.0)

                # 3. Open paper trades for top 5 eligible stocks
                trades_opened = 0
                for stock in all_results:
                    if trades_opened >= 5:
                        break

                    # Eligibility: HC or score >= 65
                    score = stock.get("score", 0)
                    hc = stock.get("high_conviction", False)
                    if not hc and score < 65:
                        continue

                    # Must have valid price, target, stop
                    if not stock.get("price") or stock["price"] <= 0:
                        continue
                    if not stock.get("target_price") or not stock.get("stop_loss"):
                        continue

                    trade_id = db.create_paper_trade(
                        {**stock,
                         "_entry_rank": trades_opened + 1,
                         "_breadth_advances": advances,
                         "_breadth_declines": declines,
                         "_breadth_ratio": breadth_ratio},
                        nifty_price, regime
                    )
                    if trade_id:
                        trades_opened += 1

                # 3. Save daily equity curve
                db.save_portfolio_daily(nifty_price)

                log.info("[PaperTrade] Daily snapshot: %d trades opened, top 20 saved", trades_opened)
                time.sleep(600)  # sleep 10 min after snapshot
            else:
                time.sleep(60)  # check every minute

        except Exception as exc:
            log.warning("[PaperTrade] Snapshot error: %s", exc)
            time.sleep(300)


# ---------------------------------------------------------------------------
# Release 3: Outcome Checker (every 30 min during market hours)
# ---------------------------------------------------------------------------

def _outcome_checker_loop():
    """
    Every 30 minutes during market hours:
    1. Get all open paper trades
    2. Fetch current prices
    3. Update max drawdown / max runup
    4. Close trades that hit target, stop, or time limit (20 trading days)
    """
    time.sleep(300)  # 5 min startup grace

    while True:
        try:
            if not live_feed.is_market_open():
                time.sleep(300)
                continue

            open_trades = db.get_open_paper_trades()
            if not open_trades:
                time.sleep(1800)
                continue

            # Get Nifty for alpha calc
            nifty_price = None
            try:
                nifty_meta = db.get_meta("nifty50_price")
                if nifty_meta:
                    nifty_price = float(nifty_meta)
            except Exception:
                pass

            closed_count = 0
            
            # Fetch prices and update Research Lifecycle Engine (Phase 6)
            prices_for_lifecycle = {}
            for trade in open_trades:
                sym = trade["symbol"]
                p_data = live_feed.get_live_price(sym)
                if p_data:
                    ltp = p_data.get("ltp") or p_data.get("price", 0)
                    if ltp and ltp > 0:
                        prices_for_lifecycle[sym] = ltp
                        
            # Execute Phase 6 Lifecycle engine
            db.update_research_lifecycle_outcomes(prices_for_lifecycle)
            
            for trade in open_trades:
                sym = trade["symbol"]
                trade_id = trade["id"]

                # Use WebSocket cache (instant) instead of REST bulk fetch (0.5s/sym)
                ltp = prices_for_lifecycle.get(sym)
                if not ltp:
                    continue

                # Update extremes
                db.update_paper_trade_extremes(trade_id, ltp)

                # Check exit conditions
                target = trade.get("target_price")
                stop = trade.get("stop_loss")

                # Days held
                from datetime import date as _date
                try:
                    entry_dt = _date.fromisoformat(trade["entry_date"])
                    days_held = (_date.today() - entry_dt).days
                except Exception:
                    days_held = 0

                exit_reason = None

                if target and ltp >= target:
                    exit_reason = "TARGET_HIT"
                elif stop and ltp <= stop:
                    exit_reason = "STOP_HIT"
                elif days_held >= 20:  # 20 trading day max hold
                    exit_reason = "TIME_EXIT"

                if exit_reason:
                    db.close_paper_trade(trade_id, ltp, exit_reason, nifty_price)
                    closed_count += 1

            if closed_count > 0:
                db.save_portfolio_daily(nifty_price)

            log.info("[PaperTrade] Outcome check: %d open, %d closed", len(open_trades), closed_count)

        except Exception as exc:
            log.warning("[PaperTrade] Outcome check error: %s", exc)

        time.sleep(1800)  # every 30 mins


threading.Thread(target=_auto_scan_loop, daemon=True, name="auto-scan").start()
log.info("Auto-scan enabled: every %d minutes", AUTO_SCAN_INTERVAL)

threading.Thread(target=_portfolio_scan_loop, daemon=True, name="portfolio-scan").start()
log.info("Portfolio-scan enabled: every 30 minutes")

# Release 3: Paper trading threads
threading.Thread(target=_paper_trade_snapshot_loop, daemon=True, name="paper-trade-snapshot").start()
log.info("Paper-trade snapshot enabled: daily at 11:00 AM IST")

threading.Thread(target=_outcome_checker_loop, daemon=True, name="outcome-checker").start()
log.info("Outcome checker enabled: every 30 minutes during market hours")

# Phase 8: Start MarketAux background worker
from scanner import start_marketaux_worker
start_marketaux_worker()

# Phase 2: Start Active Watchdog (Section 3, 8)
from watchdog import start_watchdog
_watchdog_thread = start_watchdog(_shutdown_event)
log.info("Watchdog started (Section 3: active stale scan recovery)")


# ─── Phase 0B: Graceful Shutdown Handler (Section 12) ─────────────────
def _graceful_shutdown(signum, frame):
    """Handle SIGTERM/SIGINT for graceful process termination.
    Section 12: On shutdown:
    1. Set shutdown event for all daemon threads
    2. If a scan is active, transition it to FAILED
    3. Stop the watchdog
    """
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    log.warning("[SHUTDOWN] Signal %s received — initiating graceful shutdown", sig_name)

    # 1. Signal all threads to stop
    _shutdown_event.set()

    # 2. Flush any active scan to FAILED
    try:
        active, active_scan_id = db.is_scan_active()
        if active:
            log.warning("[SHUTDOWN] Active scan %s — transitioning to FAILED", active_scan_id)
            db.transition_scan_state(
                scan_id=active_scan_id,
                from_status="running",
                to_status="failed",
                reason="graceful_shutdown",
                actor="system",
                error_message=f"Process terminated by {sig_name}",
            )
    except Exception as exc:
        log.error("[SHUTDOWN] Failed to flush active scan: %s", exc)

    # 3. Stop watchdog
    try:
        from watchdog import stop_watchdog
        stop_watchdog()
    except Exception:
        pass

    log.warning("[SHUTDOWN] Graceful shutdown complete")


# Register signal handlers
signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)
# Windows-specific: SIGBREAK (Ctrl+Break)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, _graceful_shutdown)
log.info("Signal handlers registered (SIGTERM, SIGINT) for graceful shutdown")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    app.run(debug=False, host="0.0.0.0", port=port)
