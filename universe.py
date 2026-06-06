"""
universe.py — Smart Stock Universe Manager (Phase 1.5)

Provides ACTIVE_UNIVERSE for the scanner based on layered filters:

  Layer 1 — NSE F&O universe (~200 most liquid stocks, always reliable data)
  Layer 2 — Curated sector universe (stocks.py _HARDCODED_UNIVERSE, ~573 stocks)
  Layer 3 — User portfolio holdings (OPEN positions in DB)
  Layer 4 — User custom watchlist stocks

ACTIVE_UNIVERSE = union of all enabled layers, deduplicated and sorted.

Environment Variables:
  FULL_UNIVERSE=0  (default) — curated ~573 stocks
  FULL_UNIVERSE=1            — all tokens from angel_tokens.json (2200+)

Output file: cache/active_universe.json
  Versioned structure with version=5, count, updated_at, symbols.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Set

log = logging.getLogger("screener")

# Paths
ACTIVE_FILE = Path(__file__).parent / "cache" / "active_universe.json"

# Layer enable flags — all True by default
ENABLE_FNO_UNIVERSE = True
ENABLE_SECTOR_UNIVERSE = True
ENABLE_PORTFOLIO_STOCKS = True
ENABLE_CUSTOM_STOCKS = True

# ─── NSE F&O Universe (~200 most liquid, most reliable data) ───
# These are the stocks where Angel One / jugaad_data data quality
# is most consistent and volume ensures valid technical signals.

FNO_UNIVERSE: Set[str] = {
    # Nifty 50 core
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC", "LT", "AXISBANK",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "HCLTECH", "SUNPHARMA",
    "TITAN", "WIPRO", "ULTRACEMCO", "NTPC", "POWERGRID", "NESTLEIND",
    "TECHM", "ONGC", "TATASTEEL", "JSWSTEEL", "INDUSINDBK", "HINDALCO",
    "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "GRASIM", "CIPLA", "DRREDDY",
    "COALINDIA", "BPCL", "EICHERMOT", "DIVISLAB", "BRITANNIA",
    "APOLLOHOSP", "HEROMOTOCO", "SBILIFE", "DABUR", "HDFCLIFE",
    "BAJAJ-AUTO", "TATACONSUM", "PIDILITIND", "SIEMENS", "ADANIGREEN",
    # Nifty Next 50 / F&O liquids
    "HAVELLS", "AMBUJACEM", "DLF", "GODREJCP", "TRENT", "VEDL",
    "BANKBARODA", "IOC", "ICICIPRULI", "INDIGO", "ABB", "SRF",
    "NAUKRI", "TORNTPHARM", "GAIL", "PIIND", "HINDPETRO", "MARICO",
    "PERSISTENT", "TATAPOWER", "COLPAL", "CANBK", "BERGEPAINT",
    "MPHASIS", "PFC", "RECLTD", "IDFCFIRSTB", "LUPIN", "AUROPHARMA",
    "VOLTAS", "POLYCAB", "SHREECEM", "TVSMOTOR", "SAIL", "MRF",
    "FEDERALBNK", "CUMMINSIND", "CONCOR", "PETRONET", "ABCAPITAL",
    "NMDC", "JUBLFOOD", "COFORGE", "ALKEM", "ASTRAL", "MAXHEALTH",
    "OFSS", "IRCTC", "CROMPTON", "BHARATFORG", "LICHSGFIN", "AUBANK",
    "DEEPAKNTR", "DIXON", "ESCORTS", "BIOCON", "BALKRISIND",
    "LTTS", "KPITTECH", "SYNGENE", "BEL", "SUPREMEIND", "PAGEIND",
    "HINDCOPPER", "PHOENIXLTD", "ZYDUSLIFE", "UBL", "RAMCOCEM",
    "TATAELXSI", "BATAINDIA", "THERMAX", "SUNDARMFIN", "SUNTV",
    "MUTHOOTFIN", "M&MFIN", "YESBANK", "MANAPPURAM", "IEX",
    "NATIONALUM", "SONACOMS", "METROPOLIS", "FORTIS", "KAJARIACER",
    "CHOLAFIN", "HAL", "BDL", "MAZDOCK", "NHPC", "SJVN", "IRFC",
    "JSWENERGY", "CGPOWER", "KAYNES", "SUZLON", "GODREJPROP",
    "CANFINHOME", "ANGELONE", "PVRINOX", "BLUEDART", "DATAPATTNS",
    "LALPATHLAB", "AFFLE", "ZEEL", "ABFRL", "CHAMBLFERT",
    "IDEA", "M&M", "TATAMOTORS", "JINDALSTEL",
}


# ─── Public API ───

def get_active_universe(
    include_portfolio: bool = True,
    include_custom: bool = True,
) -> list:
    """
    Returns the ACTIVE_UNIVERSE as a sorted, deduplicated list.

    Priority layers (union — all included, no hierarchy):
      1. F&O universe (always, most liquid NSE stocks)
      2. _HARDCODED_UNIVERSE from stocks.py (curated 573 NSE stocks)
      3. Open portfolio positions (if include_portfolio=True)
      4. Custom watchlist stocks (if include_custom=True)
    """
    active: Set[str] = set()

    if ENABLE_FNO_UNIVERSE:
        active.update(FNO_UNIVERSE)

    if ENABLE_SECTOR_UNIVERSE:
        try:
            from stocks import _HARDCODED_UNIVERSE
            active.update(_HARDCODED_UNIVERSE)
        except Exception as exc:
            log.warning("Failed to load _HARDCODED_UNIVERSE from stocks.py: %s", exc)

    if include_portfolio and ENABLE_PORTFOLIO_STOCKS:
        try:
            import db
            positions = db.execute_db(
                "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'",
                fetch="all"
            )
            if positions:
                active.update(r["symbol"] for r in positions if r.get("symbol"))
        except Exception as exc:
            log.debug("Portfolio universe layer failed (non-fatal): %s", exc)

    if include_custom and ENABLE_CUSTOM_STOCKS:
        try:
            import db
            customs = db.get_custom_stocks()
            active.update(s["symbol"] for s in customs if s.get("symbol"))
        except Exception as exc:
            log.debug("Custom stocks universe layer failed (non-fatal): %s", exc)

    return sorted(active)


def get_fast_scan_universe() -> list:
    """
    Return the universe to use for fast scans.

    Default (FULL_UNIVERSE=0): curated ~573 stocks from get_active_universe().
    Override (FULL_UNIVERSE=1): all tokens from angel_tokens.json (2200+).

    Always appends open portfolio positions and custom stocks regardless of mode.
    """
    if os.getenv("FULL_UNIVERSE", "0") == "1":
        log.info("FULL_UNIVERSE=1: loading all angel_tokens symbols")
        try:
            from stocks import STOCK_UNIVERSE
            full = list(STOCK_UNIVERSE)
            # Still add portfolio + custom on top
            try:
                import db
                positions = db.execute_db(
                    "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'",
                    fetch="all"
                )
                if positions:
                    extras = {r["symbol"] for r in positions if r.get("symbol")}
                    full = list(dict.fromkeys(full + [s for s in extras if s not in set(full)]))
            except Exception:
                pass
            return full
        except Exception as exc:
            log.warning("Full universe load failed, falling back to curated: %s", exc)

    return get_active_universe()


def save_active_universe(symbols: list) -> None:
    """
    Persist the resolved universe to cache/active_universe.json.
    Versioned structure (version=5) for visibility and debugging.
    """
    ACTIVE_FILE.parent.mkdir(exist_ok=True)
    payload = {
        "version": 5,
        "count": len(symbols),
        "updated_at": datetime.now().isoformat(),
        "symbols": symbols,
    }
    ACTIVE_FILE.write_text(json.dumps(payload, indent=2))
    log.debug("Active universe saved: %d symbols → %s", len(symbols), ACTIVE_FILE)


def get_universe_stats() -> dict:
    """
    Return a lightweight stats dict for /api/universe and /api/health.
    No DB calls — uses cached file if available.
    """
    cached_count = None
    cached_at = None
    if ACTIVE_FILE.exists():
        try:
            data = json.loads(ACTIVE_FILE.read_text())
            cached_count = data.get("count")
            cached_at = data.get("updated_at")
        except Exception:
            pass

    return {
        "active_count": cached_count or len(get_active_universe()),
        "fno_count": len(FNO_UNIVERSE),
        "curated_count": _curated_count(),
        "full_universe_enabled": os.getenv("FULL_UNIVERSE", "0") == "1",
        "last_updated": cached_at,
    }


def _curated_count() -> int:
    """Count of _HARDCODED_UNIVERSE without importing DB."""
    try:
        from stocks import _HARDCODED_UNIVERSE
        return len(_HARDCODED_UNIVERSE)
    except Exception:
        return 0
