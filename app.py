#!/usr/bin/env python3
"""
Smart Screener — Entry Point
NSE Stock Screener + Portfolio Manager with Angel One Live Feed
"""

import os
import time
import logging
import threading

from dotenv import load_dotenv
load_dotenv()  # must run before any config import that reads env vars

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

# Pre-create jugaad_data cache dirs to avoid race condition
for d in [os.path.expanduser("~/.cache/nsehistory-stock"),
          os.path.expanduser("~/.cache/nsehistory-index")]:
    os.makedirs(d, exist_ok=True)

import db
import auth_db
import live_feed
from config import AUTO_SCAN_INTERVAL, FLASK_SECRET_KEY
from scanner import scan_state, has_valid_cache, run_full_scan
from routes.pages import pages_bp
from routes.api import api_bp
from routes.portfolio import portfolio_bp
from routes.auth import auth_bp, init_oauth
from routes.admin import admin_bp

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

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(api_bp)
app.register_blueprint(portfolio_bp)

# Initialize Google OAuth client
init_oauth(app)


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
    log.info("No valid cache. Starting first scan...")
    threading.Thread(target=run_full_scan, daemon=True).start()


# ---------------------------------------------------------------------------
# Auto-scan scheduler
# ---------------------------------------------------------------------------
def _auto_scan_loop():
    interval = AUTO_SCAN_INTERVAL * 60
    time.sleep(60)
    while True:
        try:
            if scan_state.is_scanning:
                log.info("[AutoScan] Scan already running, skipping")
            elif live_feed.is_market_open():
                log.info("[AutoScan] Market open — starting scan")
                run_full_scan()
            else:
                last = db.get_meta("last_scan")
                if not last:
                    log.info("[AutoScan] No data yet — starting scan")
                    run_full_scan()
                else:
                    log.debug("[AutoScan] Market closed, data fresh — sleeping")
        except Exception as exc:
            log.warning("[AutoScan] Error: %s", exc)
        time.sleep(interval)


def _portfolio_scan_loop():
    time.sleep(120)  # wait 2 mins for startup
    while True:
        try:
            log.info("[PortfolioScan] Running 30-min portfolio check...")
            positions = db.execute_db("SELECT id, symbol, buy_price, stop_loss, target FROM positions WHERE status = 'OPEN'", fetch="all")
            if positions:
                symbols = list(set(p["symbol"] for p in positions))
                prices = live_feed.fetch_ltp_bulk(symbols)
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


threading.Thread(target=_auto_scan_loop, daemon=True, name="auto-scan").start()
log.info("Auto-scan enabled: every %d minutes", AUTO_SCAN_INTERVAL)

threading.Thread(target=_portfolio_scan_loop, daemon=True, name="portfolio-scan").start()
log.info("Portfolio-scan enabled: every 30 minutes")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=False, host="0.0.0.0", port=port)
