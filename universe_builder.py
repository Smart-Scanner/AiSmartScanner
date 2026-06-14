"""
universe_builder.py — Phase 5.5: Eligible Universe Builder

Replaces direct hardcoded universe usage with a filtered, versioned
eligible universe derived from universe_catalog (Stock Master Registry).

Filters (Turnover = Primary, Volume = Secondary):
  - Market Cap > UNIVERSE_MIN_MCAP_CR (1500 Cr)
  - 20 Day Avg Turnover > UNIVERSE_MIN_AVG_TURNOVER_CR (5 Cr) — PRIMARY
  - 20 Day Avg Volume > UNIVERSE_MIN_AVG_VOLUME (100,000) — secondary
  - Price > UNIVERSE_MIN_PRICE (20)
  - Not ETF
  - Not SME
  - Not Suspended (is_active = True)

Always Include:
  - Open portfolio positions
  - User watchlist symbols

Schedule: Daily at 8:30 AM IST (called from app.py)

Output:
  - Persisted to `eligible_universe` table
  - Versioned: UNIVERSE_v001, UNIVERSE_v002, ...
"""

import logging
import re
from datetime import datetime

log = logging.getLogger("screener")


def build_eligible_universe() -> tuple[list[str], str]:
    import db
    db.set_meta("universe_build_status", "BUILDING")
    try:
        symbols, version = _build_eligible_universe_impl()
        db.set_meta("universe_build_status", "READY")
        return symbols, version
    except Exception:
        db.set_meta("universe_build_status", "FAILED")
        raise


def _build_eligible_universe_impl() -> tuple[list[str], str]:
    """
    Build the eligible universe from universe_catalog.

    1. Read all active stocks from universe_catalog
    2. Apply eligibility filters (turnover primary, volume secondary)
    3. Force-include: open portfolio positions + custom watchlist
    4. Generate version: UNIVERSE_vNNN (auto-increment)
    5. Save to eligible_universe table
    6. Return (eligible_symbols, universe_version)

    If universe_catalog is empty or too small, falls back to the
    curated universe from universe.py (existing behavior).
    """
    import db
    from config import (
        UNIVERSE_MIN_MCAP_CR, UNIVERSE_MIN_AVG_TURNOVER_CR,
        UNIVERSE_MIN_AVG_VOLUME, UNIVERSE_MIN_PRICE,
        UNIVERSE_MIN_LISTING_DAYS,
    )

    # 1. Read universe_catalog
    catalog = db.get_universe_catalog_eligible()
    if not catalog or len(catalog) < 50:
        log.warning("[UniverseBuilder] Catalog too small (%d), falling back to curated universe",
                    len(catalog) if catalog else 0)
        from universe import get_active_universe
        symbols = get_active_universe()
        version = _next_version()
        _save_fallback_universe(symbols, version)
        return symbols, version

    log.info("[UniverseBuilder] Processing %d catalog stocks", len(catalog))

    # 2. Apply eligibility filters
    eligible_data = []
    rejected = {"mcap": 0, "turnover": 0, "volume": 0, "price": 0,
                "etf": 0, "sme": 0, "suspended": 0, "ipo_age": 0}

    for stock in catalog:
        symbol = stock.get("symbol", "")
        if not symbol:
            continue

        mcap = stock.get("market_cap") or 0
        avg_turnover = stock.get("avg_turnover_20d") or 0
        avg_volume = stock.get("avg_volume_20d") or 0
        price = stock.get("price") or 0
        instrument = (stock.get("instrument_type") or "EQ").upper()
        is_active = stock.get("is_active", True)

        # Skip suspended
        if not is_active:
            rejected["suspended"] += 1
            continue

        # Skip ETF
        if instrument in ("ETF", "INDEX", "MF"):
            rejected["etf"] += 1
            continue

        # Skip SME
        if instrument == "SME" or _is_sme_symbol(symbol):
            rejected["sme"] += 1
            continue

        # Market Cap filter
        mcap_cr = mcap / 1e7 if mcap > 10000 else mcap  # normalize if in absolute
        if mcap_cr > 0 and mcap_cr < UNIVERSE_MIN_MCAP_CR:
            rejected["mcap"] += 1
            continue

        # PRIMARY: Turnover filter (₹ Cr per day)
        turnover_cr = avg_turnover / 1e7 if avg_turnover > 10000 else avg_turnover
        if turnover_cr > 0 and turnover_cr < UNIVERSE_MIN_AVG_TURNOVER_CR:
            rejected["turnover"] += 1
            continue

        # SECONDARY: Volume filter
        if avg_volume > 0 and avg_volume < UNIVERSE_MIN_AVG_VOLUME:
            rejected["volume"] += 1
            continue

        # Price filter
        if price > 0 and price < UNIVERSE_MIN_PRICE:
            rejected["price"] += 1
            continue

        # IPO Age filter — DISABLED until real listing_date is available.
        #
        # BUG: first_seen_at = CURRENT_TIMESTAMP at first master sync, so ALL stocks
        # get age=0 days on initial run (Reliance, TCS, HDFC all rejected).
        # first_seen_at tracks "when MarketOS discovered it", NOT actual listing date.
        #
        # TODO: Re-enable when master_sync stores actual listing_date from yfinance
        #       (info.get("ipoDate") or earliest available OHLCV date).
        #       Then use: listing_date-based filter instead of first_seen_at.
        #
        # if listing_date and UNIVERSE_MIN_LISTING_DAYS > 0:
        #     age = (datetime.now() - listing_date).days
        #     if age < UNIVERSE_MIN_LISTING_DAYS:
        #         rejected["ipo_age"] += 1
        #         continue

        reason = "FILTER_PASS"
        eligible_data.append({
            "symbol": symbol,
            "market_cap_cr": mcap_cr,
            "avg_volume_20d": avg_volume,
            "avg_turnover_20d": turnover_cr,
            "price": price,
            "eligibility_reason": reason,
        })

    log.info("[UniverseBuilder] Filter results: passed=%d | rejected: mcap=%d turnover=%d "
             "volume=%d price=%d etf=%d sme=%d suspended=%d ipo_age=%d",
             len(eligible_data), rejected["mcap"], rejected["turnover"],
             rejected["volume"], rejected["price"], rejected["etf"],
             rejected["sme"], rejected["suspended"], rejected["ipo_age"])

    # 3. Force-include portfolio + watchlist
    eligible_symbols = {s["symbol"] for s in eligible_data}
    force_included = 0

    try:
        positions = db.execute_db(
            "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'",
            fetch="all"
        )
        if positions:
            for p in positions:
                sym = p.get("symbol")
                if sym and sym not in eligible_symbols:
                    eligible_data.append({
                        "symbol": sym,
                        "market_cap_cr": 0,
                        "avg_volume_20d": 0,
                        "avg_turnover_20d": 0,
                        "price": 0,
                        "eligibility_reason": "PORTFOLIO_FORCE_INCLUDE",
                    })
                    eligible_symbols.add(sym)
                    force_included += 1
    except Exception:
        pass

    try:
        customs = db.get_custom_stocks()
        if customs:
            for c in customs:
                sym = c.get("symbol")
                if sym and sym not in eligible_symbols:
                    eligible_data.append({
                        "symbol": sym,
                        "market_cap_cr": 0,
                        "avg_volume_20d": 0,
                        "avg_turnover_20d": 0,
                        "price": 0,
                        "eligibility_reason": "WATCHLIST_FORCE_INCLUDE",
                    })
                    eligible_symbols.add(sym)
                    force_included += 1
    except Exception:
        pass

    if force_included:
        log.info("[UniverseBuilder] Force-included %d portfolio/watchlist stocks", force_included)

    # 4. Generate version
    version = _next_version()

    # 5. Save to DB
    db.save_eligible_universe(eligible_data, version)

    symbols = sorted(eligible_symbols)
    log.info("[UniverseBuilder] Eligible universe: %d stocks, version=%s", len(symbols), version)

    # 6. Record rebuild history for Mission Control / drift debugging
    try:
        db.save_universe_rebuild_history(
            version=version,
            input_count=len(catalog),
            eligible_count=len(symbols),
            rejected=rejected,
            force_included=force_included,
            fallback_used=False,
        )
    except Exception as exc:
        log.debug("[UniverseBuilder] Failed to save rebuild history (non-fatal): %s", exc)

    return symbols, version


def _next_version() -> str:
    """Generate next UNIVERSE_vNNN version string."""
    import db
    current = db.get_latest_universe_version()
    # Extract number from UNIVERSE_vNNN
    match = re.search(r"v(\d+)", current)
    if match:
        num = int(match.group(1)) + 1
    else:
        num = 1
    return f"UNIVERSE_v{num:03d}"


def _is_sme_symbol(symbol: str) -> bool:
    """Heuristic: detect SME exchange symbols."""
    # SME stocks typically have 'SME' suffix or are on BSE SME platform
    return symbol.endswith("SME") or "-SM" in symbol


def _save_fallback_universe(symbols: list, version: str):
    """Save a fallback universe when catalog is unavailable."""
    import db
    data = [{"symbol": s, "market_cap_cr": 0, "avg_volume_20d": 0,
             "avg_turnover_20d": 0, "price": 0,
             "eligibility_reason": "FALLBACK_CURATED"} for s in symbols]
    db.save_eligible_universe(data, version)


def refresh_volume_turnover_metrics(symbols: list = None) -> int:
    """
    For each symbol in universe_catalog, compute 20-day avg volume
    and turnover from the last 30 days of historical OHLCV data.

    Updates universe_catalog.avg_volume_20d / avg_turnover_20d / price.
    Returns count of symbols updated.

    This is a heavy operation — called during Master Sync or
    pre-scan warmup, not per-scan.
    """
    import db
    import live_feed

    if symbols is None:
        catalog = db.get_universe_catalog_eligible()
        symbols = [s.get("symbol") for s in catalog if s.get("symbol")]

    updated = 0
    for sym in symbols:
        try:
            df = live_feed.fetch_historical(sym, days=30)
            if df is None or df.empty or len(df) < 10:
                continue

            # Last 20 trading days
            recent = df.tail(20)
            avg_volume = float(recent["VOLUME"].mean())
            # Turnover = Volume × Close price (approximate)
            recent_turnover = recent["VOLUME"] * recent["CLOSE"]
            avg_turnover = float(recent_turnover.mean())
            last_price = float(recent["CLOSE"].iloc[-1])

            db.update_universe_catalog_metrics(sym, avg_volume, avg_turnover, last_price)
            updated += 1
        except Exception as exc:
            log.debug("[UniverseBuilder] Metrics update failed for %s: %s", sym, exc)

    log.info("[UniverseBuilder] Updated volume/turnover metrics for %d symbols", updated)
    return updated
