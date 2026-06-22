"""
nse_bhavcopy.py — Phase 2: FREE Bulk Market Data from NSE/BSE Bhavcopy
========================================================================
Downloads daily bhavcopy CSVs (FREE, no rate limit, ALL stocks in one file):
- NSE: volume, turnover, OHLCV, delivery %
- BSE: market_cap (BSE provides this!)

Enriches universe_catalog with latest market data.
Builds eligible_universe with filters.

Schedule: Boot + daily 18:30 IST (after market close)
"""

import io
import csv
import logging
import zipfile
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("screener")

# ─── NSE Bhavcopy ────────────────────────────────────────────────────────────

def _get_nse_bhavcopy_url(dt: datetime) -> str:
    """Generate NSE bhavcopy URL for a given date."""
    # New NSE format (post 2024)
    date_str = dt.strftime("%Y%m%d")
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"


def _get_nse_headers() -> dict:
    """NSE requires browser-like headers."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/",
    }


def fetch_nse_bhavcopy(target_date: datetime = None) -> list:
    """
    Fetch NSE bhavcopy CSV for a given date.
    Returns list of dicts: [{symbol, open, high, low, close, volume, turnover, trades}, ...]
    
    Falls back to previous trading days if current date not available.
    """
    import requests

    if target_date is None:
        target_date = datetime.now()

    session = requests.Session()
    # First hit NSE homepage to get cookies (NSE blocks without session cookies)
    try:
        session.get("https://www.nseindia.com/", headers=_get_nse_headers(), timeout=10)
        time.sleep(1)  # NSE needs a small delay after cookie fetch
    except Exception:
        pass

    # Try last 7 trading days (handles weekends + holidays)
    for days_back in range(7):
        dt = target_date - timedelta(days=days_back)
        if dt.weekday() >= 5:  # Skip weekends
            continue

        url = _get_nse_bhavcopy_url(dt)
        log.info("[Bhavcopy] Fetching NSE bhavcopy for %s...", dt.strftime("%Y-%m-%d"))

        try:
            resp = session.get(url, headers=_get_nse_headers(), timeout=30)
            if resp.status_code != 200:
                log.debug("[Bhavcopy] NSE %s returned %d", dt.strftime("%Y-%m-%d"), resp.status_code)
                continue

            # Unzip and parse CSV
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            csv_name = zf.namelist()[0]
            csv_data = zf.read(csv_name).decode("utf-8")

            reader = csv.DictReader(io.StringIO(csv_data))
            records = []

            for row in reader:
                symbol = row.get("TckrSymb", "").strip()
                series = row.get("SctySrs", "").strip()

                # Only keep EQ series (skip BE, BZ, etc.)
                if series != "EQ":
                    continue

                if not symbol:
                    continue

                try:
                    records.append({
                        "symbol": symbol,
                        "series": series,
                        "open": float(row.get("OpnPric", 0) or 0),
                        "high": float(row.get("HghPric", 0) or 0),
                        "low": float(row.get("LwPric", 0) or 0),
                        "close": float(row.get("ClsPric", 0) or 0),
                        "volume": int(float(row.get("TtlTradgVol", 0) or 0)),
                        "turnover": float(row.get("TtlTrfVal", 0) or 0),
                        "trades": int(float(row.get("TtlNbOfTxsExctd", 0) or 0)),
                        "date": dt.strftime("%Y-%m-%d"),
                    })
                except (ValueError, TypeError):
                    continue

            if records:
                log.info("[Bhavcopy] ✅ NSE bhavcopy: %d EQ records for %s",
                         len(records), dt.strftime("%Y-%m-%d"))
                return records

        except Exception as exc:
            log.warning("[Bhavcopy] NSE fetch failed for %s: %s", dt.strftime("%Y-%m-%d"), exc)
            continue

    log.warning("[Bhavcopy] Could not fetch NSE bhavcopy for any recent date")
    return []


# ─── BSE Bhavcopy (has market cap!) ──────────────────────────────────────────

def fetch_bse_bhavcopy(target_date: datetime = None) -> list:
    """
    Fetch BSE bhavcopy for market cap data.
    BSE provides market_cap in their equity bhavcopy!
    
    Returns list of dicts: [{bse_code, name, close, volume, turnover_cr, date}, ...]
    """
    import requests

    if target_date is None:
        target_date = datetime.now()

    # Try last 7 trading days
    for days_back in range(7):
        dt = target_date - timedelta(days=days_back)
        if dt.weekday() >= 5:
            continue

        # BSE equity bhavcopy URL
        date_str = dt.strftime("%d%m%y")  # ddmmyy format
        url = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{date_str}_CSV.ZIP"

        log.info("[Bhavcopy] Fetching BSE bhavcopy for %s...", dt.strftime("%Y-%m-%d"))

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bseindia.com/",
            }
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                log.debug("[Bhavcopy] BSE %s returned %d", dt.strftime("%Y-%m-%d"), resp.status_code)
                continue

            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            csv_name = zf.namelist()[0]
            csv_data = zf.read(csv_name).decode("utf-8")

            reader = csv.DictReader(io.StringIO(csv_data))
            records = []

            for row in reader:
                sc_name = row.get("SC_NAME", "").strip()
                sc_code = row.get("SC_CODE", "").strip()
                sc_type = row.get("SC_TYPE", "").strip()

                # Only equity
                if sc_type not in ("Q", "A", "B", ""):
                    continue

                try:
                    close = float(row.get("CLOSE", 0) or 0)
                    volume = int(float(row.get("NO_OF_SHRS", 0) or 0))
                    turnover = float(row.get("NET_TURNOV", 0) or 0)

                    records.append({
                        "bse_code": sc_code,
                        "name": sc_name,
                        "close": close,
                        "volume": volume,
                        "turnover_cr": turnover / 1e7,  # Convert to crores
                        "date": dt.strftime("%Y-%m-%d"),
                    })
                except (ValueError, TypeError):
                    continue

            if records:
                log.info("[Bhavcopy] ✅ BSE bhavcopy: %d records for %s",
                         len(records), dt.strftime("%Y-%m-%d"))
                return records

        except Exception as exc:
            log.warning("[Bhavcopy] BSE fetch failed for %s: %s", dt.strftime("%Y-%m-%d"), exc)
            continue

    log.warning("[Bhavcopy] Could not fetch BSE bhavcopy for any recent date")
    return []


# ─── Enrichment: Update universe_catalog with bhavcopy data ─────────────────

def enrich_universe_from_bhavcopy() -> dict:
    """
    Download today's bhavcopy and enrich universe_catalog with:
    - price (close)
    - volume (as avg_volume_20d approximation for day-1)
    - turnover (as avg_turnover_20d approximation for day-1)
    
    Returns summary dict.
    """
    import db

    log.info("[Bhavcopy] Starting universe enrichment from bhavcopy...")

    # 1. Fetch NSE bhavcopy
    nse_data = fetch_nse_bhavcopy()

    if not nse_data:
        log.warning("[Bhavcopy] No NSE bhavcopy data. Skipping enrichment.")
        return {"nse_enriched": 0, "error": "no_nse_data"}

    # 2. Enrich universe_catalog — update price, volume, turnover
    enriched = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for record in nse_data:
        try:
            # Use ? placeholders — execute_db translates to %s for PG
            result = db.execute_db(
                """UPDATE universe_catalog SET 
                     price = ?,
                     avg_volume_20d = ?,
                     avg_turnover_20d = ?,
                     last_synced_at = ?
                   WHERE symbol = ? AND is_active = TRUE""",
                (record["close"], record["volume"], record["turnover"],
                 now, record["symbol"]),
                fetch="rowcount"
            )
            if result and result > 0:
                enriched += 1
        except Exception as exc:
            log.debug("[Bhavcopy] Failed to enrich %s: %s", record["symbol"], exc)

    log.info("[Bhavcopy] ✅ Enriched %d/%d symbols from NSE bhavcopy", enriched, len(nse_data))

    return {
        "nse_total": len(nse_data),
        "nse_enriched": enriched,
        "date": nse_data[0]["date"] if nse_data else None,
    }


# ─── Build Eligible Universe ─────────────────────────────────────────────────

def build_eligible_universe() -> dict:
    """
    Build eligible_universe directly from enriched universe_catalog.
    
    Filters (user-specified thresholds from config.py):
    - instrument_type = 'EQ' (no ETF/NAV/MF)
    - price >= 50 (no penny stocks)
    - avg_volume_20d >= 10000 (minimum liquidity)
    - market_cap >= 1000 Cr (skip micro-caps)
    
    Uses db.save_eligible_universe() which matches the existing
    eligible_universe schema: symbol, market_cap_cr, avg_volume_20d,
    avg_turnover_20d, price, eligibility_reason, universe_version, generated_at
    """
    import db
    from config import (UNIVERSE_MIN_PRICE, UNIVERSE_MIN_AVG_VOLUME,
                        UNIVERSE_MIN_MCAP_CR)

    log.info("[Bhavcopy] Building eligible universe (price>=%s, vol>=%s, mcap>=%sCr)...",
             UNIVERSE_MIN_PRICE, UNIVERSE_MIN_AVG_VOLUME, UNIVERSE_MIN_MCAP_CR)

    # Query eligible stocks from universe_catalog
    eligible_rows = db.execute_db(
        """SELECT symbol, price, avg_volume_20d, avg_turnover_20d,
                  market_cap, sector, industry, instrument_type
           FROM universe_catalog 
           WHERE is_active = TRUE 
             AND instrument_type = 'EQ'
             AND COALESCE(price, 0) >= ?
             AND COALESCE(avg_volume_20d, 0) >= ?
             AND COALESCE(market_cap, 0) >= ?
           ORDER BY COALESCE(market_cap, 0) DESC""",
        (UNIVERSE_MIN_PRICE, UNIVERSE_MIN_AVG_VOLUME, UNIVERSE_MIN_MCAP_CR),
        fetch="all"
    )

    if not eligible_rows:
        # Fallback: if no enriched data yet (first boot before bhavcopy runs),
        # use ALL EQ stocks so the scanner has something to work with
        log.warning("[Bhavcopy] No enriched stocks found. Using ALL EQ stocks as fallback.")
        eligible_rows = db.execute_db(
            """SELECT symbol, price, avg_volume_20d, avg_turnover_20d,
                      market_cap, sector, industry, instrument_type
               FROM universe_catalog 
               WHERE is_active = TRUE AND instrument_type = 'EQ'
               ORDER BY symbol""",
            fetch="all"
        )

    if not eligible_rows:
        log.error("[Bhavcopy] No EQ stocks found in universe_catalog!")
        return {"eligible_count": 0, "error": "no_eq_stocks"}

    # Build data list matching save_eligible_universe() expected format
    # Schema: symbol, market_cap_cr, avg_volume_20d, avg_turnover_20d, 
    #         price, eligibility_reason
    eligible_data = []
    for row in eligible_rows:
        eligible_data.append({
            "symbol": row["symbol"],
            "market_cap_cr": (row.get("market_cap") or 0) / 1e7 if (row.get("market_cap") or 0) > 10000 else (row.get("market_cap") or 0),
            "avg_volume_20d": row.get("avg_volume_20d") or 0,
            "avg_turnover_20d": row.get("avg_turnover_20d") or 0,
            "price": row.get("price") or 0,
            "eligibility_reason": "BHAVCOPY_FILTER_PASS",
        })

    # Generate version
    version = f"BHAV_{datetime.now().strftime('%Y%m%d_%H%M')}"

    # Use the existing db function which handles schema correctly
    db.save_eligible_universe(eligible_data, version)

    # Set active universe version
    db.set_meta("active_universe_version", version)
    db.set_meta("universe_state", "READY")
    db.set_meta("eligible_universe_count", str(len(eligible_data)))
    db.set_meta("universe_built_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    log.info("[Bhavcopy] ✅ Eligible universe built: %d stocks (version=%s)", 
             len(eligible_data), version)

    return {
        "eligible_count": len(eligible_data),
        "version": version,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ─── Market Cap + Fundamentals Enrichment (Dhan.co — FREE, no API key) ────────

DHAN_SCANX_API = "https://ow-scanx-analytics.dhan.co/customscan/v2/fetchdt"

DHAN_FIELDS = [
    "Sym", "DispSym", "Mcap", "Pe", "Pb", "Roe", "ROCE", "Eps", "Ltp",
    "Volume", "Exch", "Ind_Pe", "DivYeild", "High1Yr", "Low1Yr",
    "DayRSI14CurrentCandle", "DaySMA50CurrentCandle", "DaySMA200CurrentCandle",
    "Isin", "Seg", "Sid", "PricePerchng1mon", "PricePerchng1year",
    "Revenue", "FreeCashFlow", "NetProfitMargin",
]

DHAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Origin": "https://dhan.co",
    "Referer": "https://dhan.co/all-stocks-list/",
}


def _fetch_dhan_exchange(exchange: str, max_pages: int = 100) -> list:
    """
    Fetch ALL stocks for given exchange (NSE/BSE) from Dhan scanx API.
    Returns list of dicts with field names mapped.
    """
    import requests

    all_stocks = []
    page = 1

    while page <= max_pages:
        payload = {
            "sort": "Mcap",
            "sorder": "desc",
            "params": [
                {"field": "OgInst", "op": "", "val": "ES"},
                {"field": "Exch", "op": "", "val": exchange},
            ],
            "fields": DHAN_FIELDS,
            "pgno": page,
        }

        try:
            resp = requests.post(
                DHAN_SCANX_API, headers=DHAN_HEADERS,
                json=payload, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", [])
            total_pages = data.get("tot_pg", 0)
            total_records = data.get("tot_rec", 0)

            if not items:
                break

            # Items are arrays — map to field names
            for item in items:
                if isinstance(item, list) and len(item) == len(DHAN_FIELDS):
                    record = dict(zip(DHAN_FIELDS, item))
                    record["_exchange"] = exchange
                    all_stocks.append(record)
                elif isinstance(item, dict):
                    item["_exchange"] = exchange
                    all_stocks.append(item)

            if page % 10 == 0:
                log.info("[Dhan] %s page %d/%d — %d stocks so far",
                         exchange, page, total_pages, len(all_stocks))

            if page >= total_pages:
                break

            page += 1
            time.sleep(0.3)  # Be nice to Dhan servers

        except Exception as exc:
            log.warning("[Dhan] %s page %d failed: %s", exchange, page, exc)
            break

    log.info("[Dhan] %s: fetched %d stocks in %d pages", exchange, len(all_stocks), page)
    return all_stocks


def _fetch_dhan_ssr_fallback() -> list:
    """
    Fallback: Parse __NEXT_DATA__ from Dhan SSR page.
    Only gets top 50 stocks (by market cap) but always works.
    """
    import requests
    from bs4 import BeautifulSoup
    import json as _json

    log.info("[Dhan] Using SSR fallback (top 50 stocks)...")

    try:
        resp = requests.get(
            "https://dhan.co/all-stocks-list/",
            headers={"User-Agent": DHAN_HEADERS["User-Agent"]},
            timeout=30
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            log.error("[Dhan] SSR fallback: __NEXT_DATA__ not found")
            return []

        data = _json.loads(script.string)
        stocks = data.get("props", {}).get("pageProps", {}).get("listData", {}).get("data", [])

        log.info("[Dhan] SSR fallback: got %d stocks", len(stocks))

        # SSR data is already dicts with proper field names
        for s in stocks:
            s["_exchange"] = s.get("Exch", "NSE")

        return stocks
    except Exception as exc:
        log.error("[Dhan] SSR fallback failed: %s", exc)
        return []


def enrich_market_cap_batch(max_symbols: int = 5000) -> dict:
    """
    Fetch market cap + fundamentals from Dhan.co for ALL NSE+BSE stocks.

    Strategy:
    1. Try Dhan POST API (paginated, all stocks) — works on Railway
    2. Fallback to __NEXT_DATA__ SSR (top 50 stocks) — always works
    3. Fallback to yfinance (slow but reliable)

    Deduplication: NSE has priority. If same ISIN exists on NSE+BSE,
    NSE data is kept. BSE-only stocks are added separately.

    Data available per stock:
    - Market Cap (Cr), PE, PB, ROE, ROCE, EPS
    - 52W High/Low, RSI, 50/200 DMA
    - Revenue, Free Cash Flow, Net Profit Margin
    - Dividend Yield, Industry PE
    """
    import db

    log.info("[MarketCap] Starting Dhan.co enrichment (NSE+BSE, NSE priority)...")

    # ── Step 1: Try Dhan POST API (all stocks, paginated) ──
    nse_stocks = _fetch_dhan_exchange("NSE")
    bse_stocks = []

    if nse_stocks:
        # Also fetch BSE
        bse_stocks = _fetch_dhan_exchange("BSE")
        log.info("[MarketCap] Dhan API: NSE=%d, BSE=%d", len(nse_stocks), len(bse_stocks))
    else:
        # API failed (DNS/network) — try SSR fallback
        log.warning("[MarketCap] Dhan API failed, trying SSR fallback...")
        nse_stocks = _fetch_dhan_ssr_fallback()

        if not nse_stocks:
            log.warning("[MarketCap] SSR also failed, using yfinance fallback...")
            return _enrich_mcap_yfinance_fallback(min(max_symbols, 200))

    # ── Step 2: Deduplicate NSE+BSE (NSE priority by ISIN) ──
    dhan_data = {}  # sym -> record

    # NSE first (priority)
    seen_isins = set()
    for stock in nse_stocks:
        sym = stock.get("Sym", "")
        isin = stock.get("Isin", "")
        mcap = stock.get("Mcap", 0) or 0

        if sym and mcap > 0:
            dhan_data[sym.upper()] = stock
            if isin:
                seen_isins.add(isin)

    # BSE: only add if ISIN not already seen (NSE priority)
    bse_added = 0
    for stock in bse_stocks:
        sym = stock.get("Sym", "")
        isin = stock.get("Isin", "")
        mcap = stock.get("Mcap", 0) or 0

        if isin and isin in seen_isins:
            continue  # Skip — NSE already has this stock

        if sym and mcap > 0 and sym.upper() not in dhan_data:
            dhan_data[sym.upper()] = stock
            bse_added += 1
            if isin:
                seen_isins.add(isin)

    log.info("[MarketCap] After dedup: %d unique stocks (NSE=%d, BSE-only=%d)",
             len(dhan_data), len(dhan_data) - bse_added, bse_added)

    if not dhan_data:
        log.warning("[MarketCap] No data from Dhan, using yfinance fallback")
        return _enrich_mcap_yfinance_fallback(min(max_symbols, 200))

    # ── Step 3: Match with universe_catalog and update ──
    all_catalog = db.execute_db(
        """SELECT symbol, isin, company_name FROM universe_catalog
           WHERE is_active = TRUE AND instrument_type = 'EQ'""",
        fetch="all"
    )

    enriched = 0
    for row in all_catalog:
        symbol = row["symbol"].upper()
        isin = (row.get("isin") or "").upper()

        # Match 1: exact symbol match (fastest, most common)
        match = dhan_data.get(symbol)

        # Match 2: ISIN match (handles symbol name differences between Angel/Dhan)
        if not match and isin:
            for dhan_sym, dhan_stock in dhan_data.items():
                if (dhan_stock.get("Isin") or "").upper() == isin:
                    match = dhan_stock
                    break

        if match:
            mcap = match.get("Mcap", 0) or 0
            if mcap <= 0:
                continue

            try:
                db.execute_db(
                    """UPDATE universe_catalog SET
                         market_cap = ?,
                         company_name = COALESCE(NULLIF(company_name, ''), ?),
                         last_synced_at = ?
                       WHERE symbol = ?""",
                    (mcap,
                     match.get("DispSym", row["symbol"]),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     row["symbol"])
                )
                enriched += 1
            except Exception as exc:
                log.debug("[MarketCap] Update failed for %s: %s", row["symbol"], exc)

    log.info("[MarketCap] ✅ Enriched %d/%d stocks with market cap from Dhan",
             enriched, len(all_catalog))

    return {
        "enriched": enriched,
        "total_catalog": len(all_catalog),
        "dhan_unique": len(dhan_data),
        "nse_count": len(nse_stocks),
        "bse_only": bse_added,
        "source": "dhan_api" if len(nse_stocks) > 50 else "dhan_ssr",
    }


def _enrich_mcap_yfinance_fallback(max_symbols: int = 100) -> dict:
    """Last-resort fallback: use yfinance if Dhan fails entirely."""
    import db

    log.info("[MarketCap] Using yfinance fallback for market cap...")

    pending = db.execute_db(
        """SELECT symbol FROM universe_catalog
           WHERE is_active = TRUE
             AND instrument_type = 'EQ'
             AND (market_cap IS NULL OR market_cap = 0)
           ORDER BY avg_turnover_20d DESC
           LIMIT ?""",
        (max_symbols,),
        fetch="all"
    )

    if not pending:
        return {"enriched": 0, "total_pending": 0, "source": "yfinance"}

    symbols = [r["symbol"] for r in pending]
    enriched = 0

    batch_size = 20
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            import yfinance as yf
            tickers_str = " ".join(f"{s}.NS" for s in batch)
            tickers = yf.Tickers(tickers_str)

            for sym in batch:
                try:
                    ticker = tickers.tickers.get(f"{sym}.NS")
                    if not ticker:
                        continue
                    info = ticker.info or {}
                    mcap = info.get("marketCap", 0) or 0
                    if mcap > 0:
                        mcap_cr = mcap / 1e7
                        db.execute_db(
                            """UPDATE universe_catalog SET
                                 market_cap = ?, last_synced_at = ?
                               WHERE symbol = ?""",
                            (mcap_cr, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sym)
                        )
                        enriched += 1
                except Exception:
                    pass
            time.sleep(1)
        except Exception:
            pass

    log.info("[MarketCap] yfinance fallback: enriched %d/%d", enriched, len(symbols))
    return {"enriched": enriched, "total_pending": len(symbols), "source": "yfinance"}


# ─── Main Entry Point (called from boot sequence) ───────────────────────────

def run_bhavcopy_pipeline() -> dict:
    """
    Complete pipeline: Universe Sync → Fetch bhavcopy → Market Cap → Build eligible universe.
    Called at boot and daily at 18:30 IST.
    """
    log.info("[Bhavcopy] ═══ Starting Bhavcopy Pipeline ═══")
    start = time.time()

    results = {}

    # Step 0: Universe Sync (filter angel_tokens → universe_catalog)
    try:
        from universe_sync import sync_universe
        sync_result = sync_universe()
        results["universe_sync"] = sync_result
        log.info("[Bhavcopy] Step 0: Universe Sync done — EQ=%s", 
                 sync_result.get("eq_count", "?"))
    except Exception as exc:
        log.error("[Bhavcopy] Universe Sync failed: %s", exc)
        results["universe_sync"] = {"error": str(exc)}

    # Step 1: Enrich from bhavcopy (price, volume, turnover — FREE, no rate limit)
    enrichment = enrich_universe_from_bhavcopy()
    results["enrichment"] = enrichment

    # Step 1.5: Market cap enrichment via yfinance (batched, background-friendly)
    # Only runs if there are stocks without market cap
    try:
        mcap_result = enrich_market_cap_batch(max_symbols=300)
        results["market_cap"] = mcap_result
        log.info("[Bhavcopy] Step 1.5: Market cap enrichment — %d stocks updated",
                 mcap_result.get("enriched", 0))
    except Exception as exc:
        log.warning("[Bhavcopy] Market cap enrichment failed (non-fatal): %s", exc)
        results["market_cap"] = {"error": str(exc)}

    # Step 2: Build eligible universe (with all filters applied)
    universe = build_eligible_universe()
    results["universe"] = universe

    duration = time.time() - start
    results["duration_sec"] = round(duration, 1)

    log.info("[Bhavcopy] ═══ Pipeline Complete in %.1fs ═══", duration)
    log.info("[Bhavcopy]   Enriched: %d | MarketCap: %d | Eligible: %d",
             enrichment.get("nse_enriched", 0),
             results.get("market_cap", {}).get("enriched", 0),
             universe.get("eligible_count", 0))

    return results

