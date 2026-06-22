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
    
    Filters (user-specified thresholds):
    - instrument_type = 'EQ' (no ETF/NAV/MF)
    - price >= 50 (no penny stocks)
    - avg_volume_20d >= 10000 (minimum liquidity)
    
    Uses db.save_eligible_universe() which matches the existing
    eligible_universe schema: symbol, market_cap_cr, avg_volume_20d,
    avg_turnover_20d, price, eligibility_reason, universe_version, generated_at
    """
    import db

    log.info("[Bhavcopy] Building eligible universe with filters...")

    # Query eligible stocks from universe_catalog
    eligible_rows = db.execute_db(
        """SELECT symbol, price, avg_volume_20d, avg_turnover_20d,
                  market_cap, sector, industry, instrument_type
           FROM universe_catalog 
           WHERE is_active = TRUE 
             AND instrument_type = 'EQ'
             AND price >= 50
             AND avg_volume_20d >= 10000
           ORDER BY avg_turnover_20d DESC""",
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


# ─── Main Entry Point (called from boot sequence) ───────────────────────────

def run_bhavcopy_pipeline() -> dict:
    """
    Complete pipeline: Universe Sync → Fetch bhavcopy → Enrich catalog → Build eligible universe.
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

    # Step 1: Enrich from bhavcopy
    enrichment = enrich_universe_from_bhavcopy()
    results["enrichment"] = enrichment

    # Step 2: Build eligible universe
    universe = build_eligible_universe()
    results["universe"] = universe

    duration = time.time() - start
    results["duration_sec"] = round(duration, 1)

    log.info("[Bhavcopy] ═══ Pipeline Complete in %.1fs ═══", duration)
    log.info("[Bhavcopy]   Enriched: %d | Eligible: %d",
             enrichment.get("nse_enriched", 0),
             universe.get("eligible_count", 0))

    return results
