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


threading.Thread(target=_auto_scan_loop, daemon=True, name="auto-scan").start()
log.info("Auto-scan enabled: every %d minutes", AUTO_SCAN_INTERVAL)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=False, host="0.0.0.0", port=port)
