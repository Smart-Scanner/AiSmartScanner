"""
master_sync.py — Phase 5.5: Master Stock Registry Sync Job

Populates universe_catalog (Stock Master Registry) from:
  1. Angel ScripMaster (angel_tokens.json) — all NSE EQ symbols
  2. yfinance metadata — market_cap, sector, industry

Schedule: Every 14 days, Sunday, 18:00 IST
Mode: Upsert only — no truncate
Resume: Tracks last_synced_at per symbol
Retry: 3 attempts with exponential backoff

Logs:
  MASTER_SYNC_STARTED
  MASTER_SYNC_COMPLETED
  MASTER_SYNC_FAILED
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger("screener")

TOKEN_FILE = Path(__file__).parent / "cache" / "angel_tokens.json"


def is_master_sync_due() -> bool:
    """Check if 14 days have passed since last master sync."""
    import db
    from config import MASTER_SYNC_INTERVAL_DAYS

    last_sync = db.get_meta("master_sync_last_completed")
    if not last_sync:
        return True

    try:
        last_dt = datetime.strptime(str(last_sync)[:19], "%Y-%m-%d %H:%M:%S")
        age_days = (datetime.now() - last_dt).days
        return age_days >= MASTER_SYNC_INTERVAL_DAYS
    except Exception:
        return True


def run_master_sync():
    """
    Master Stock Registry Sync Job — Incremental Mode.

    Phase 1: Upsert ALL NSE EQ symbols into universe_catalog (no yfinance).
             This ensures new IPOs/delistings are captured immediately.
    Phase 2: Fetch yfinance metadata ONLY for symbols where:
             - last_synced_at IS NULL (never synced), OR
             - last_synced_at > MASTER_SYNC_INTERVAL_DAYS days old
             Limited to MASTER_SYNC_DAILY_BATCH_SIZE symbols per run (default 500).
    """
    import db
    from config import MASTER_SYNC_DAILY_BATCH_SIZE, MASTER_SYNC_INTERVAL_DAYS

    # ── Reentrant lock: prevent concurrent master sync runs ──
    current_status = db.get_meta("master_sync_status")
    if current_status == "running":
        # Stale lock recovery: if running for > 30 min, treat as crashed
        started_at = db.get_meta("master_sync_started_at")
        if started_at:
            try:
                started_dt = datetime.strptime(str(started_at)[:19], "%Y-%m-%d %H:%M:%S")
                age_min = (datetime.now() - started_dt).total_seconds() / 60
                if age_min < 30:
                    log.warning("[MasterSync] Another master sync is already running (%.0f min) — skipping", age_min)
                    return {"synced": 0, "failed": 0, "skipped": True}
                else:
                    log.warning("[MasterSync] Stale lock detected (%.0f min old) — overriding", age_min)
                    db.set_meta("master_sync_status", "stale_override")
            except Exception:
                pass
        else:
            log.warning("[MasterSync] Another master sync is already running — skipping")
            return {"synced": 0, "failed": 0, "skipped": True}

    scan_id = f"master_sync_{int(time.time())}"
    log.info("[MASTER_SYNC_STARTED] scan_id=%s (incremental mode)", scan_id)
    db.set_meta("master_sync_status", "running")
    db.set_meta("master_sync_started_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.set_meta("master_sync_scan_id", scan_id)
    db.log_scan_event(scan_id, "MASTER_SYNC_STARTED", "incremental")

    start_time = time.time()

    try:
        # Phase 1: Load ALL symbols from angel_tokens.json
        all_symbols = _load_nse_symbols()
        if not all_symbols:
            raise RuntimeError("No NSE symbols found in angel_tokens.json")

        log.info("[MasterSync] Phase 1: Found %d NSE EQ symbols — upserting all into catalog",
                 len(all_symbols))

        # Bulk upsert all symbols (lightweight — no yfinance call)
        # This ensures new symbols are in the catalog even without metadata
        phase1_data = []
        for sym in all_symbols:
            phase1_data.append({
                "symbol": sym,
                "company_name": sym,
                "market_cap": 0,
                "market_cap_bucket": "Unknown Cap",
                "sector": "",
                "industry": "",
                "is_active": True,
                "instrument_type": "EQ",
                "exchange": "NSE",
            })
            # Save in batches of 100 to avoid memory pressure
            if len(phase1_data) >= 100:
                db.upsert_universe_catalog(phase1_data, set_synced_at=False)
                phase1_data = []

        if phase1_data:
            db.upsert_universe_catalog(phase1_data, set_synced_at=False)

        log.info("[MasterSync] Phase 1 done: %d symbols in catalog", len(all_symbols))

        # Phase 1.5: Classify instrument types for unsynced symbols
        # Applies name heuristics ONLY for symbols without yfinance metadata
        try:
            classified = db.classify_instrument_types()
            log.info("[MasterSync] Phase 1.5: Classified %d instrument types (heuristic)", classified)
        except Exception as exc:
            log.warning("[MasterSync] Phase 1.5: Classification failed (non-fatal): %s", exc)

        # Catalog monitoring for Mission Control
        try:
            catalog_stats = db.execute_db(
                """SELECT
                     COUNT(*) as total,
                     SUM(CASE WHEN last_synced_at IS NOT NULL THEN 1 ELSE 0 END) as synced,
                     SUM(CASE WHEN last_synced_at IS NULL THEN 1 ELSE 0 END) as pending
                   FROM universe_catalog WHERE is_active = TRUE""",
                fetch="one"
            )
            if catalog_stats:
                db.set_meta("catalog_total", str(catalog_stats.get("total", 0)))
                db.set_meta("catalog_synced", str(catalog_stats.get("synced", 0)))
                db.set_meta("catalog_pending", str(catalog_stats.get("pending", 0)))
                log.info("[MasterSync] Catalog: total=%s synced=%s pending=%s",
                         catalog_stats.get("total", 0),
                         catalog_stats.get("synced", 0),
                         catalog_stats.get("pending", 0))
        except Exception:
            pass

        # Phase 2: Resumable enrichment loop — process ALL pending symbols
        # in batches of MASTER_SYNC_DAILY_BATCH_SIZE until none remain.
        synced = 0
        failed = 0
        skipped_provider_unavailable = 0
        total_stale_processed = 0
        batch_round = 0
        batch_size = 20  # yfinance batch size

        while True:
            stale_symbols = _get_stale_symbols(MASTER_SYNC_INTERVAL_DAYS,
                                               MASTER_SYNC_DAILY_BATCH_SIZE)

            if not stale_symbols:
                if batch_round == 0:
                    log.info("[MasterSync] Phase 2: No stale symbols — skipping yfinance fetch")
                else:
                    log.info("[MasterSync] Phase 2: All stale symbols processed after %d rounds", batch_round)
                break

            batch_round += 1
            round_count = len(stale_symbols)
            total_stale_processed += round_count
            log.info("[MasterSync] PHASE2_BATCH_START round=%d stale_count=%d total_so_far=%d",
                     batch_round, round_count, total_stale_processed)

            for i in range(0, len(stale_symbols), batch_size):
                batch = stale_symbols[i:i + batch_size]
                symbols_data = []

                for sym in batch:
                    from intelligence.yf_guard import yf_is_available
                    if not yf_is_available():
                        skipped_provider_unavailable += 1
                        log.warning("[MasterSync] yfinance circuit breaker is OPEN. Skipping %s without recording failure.", sym)
                        continue

                    try:
                        meta = _fetch_symbol_metadata(sym)
                        if meta:
                            # Reset fail counter on success
                            meta["sync_fail_count"] = 0
                            symbols_data.append(meta)
                            synced += 1
                        else:
                            # Increment consecutive fail counter
                            prev_fails = _get_sync_fail_count(sym)
                            new_fails = prev_fails + 1

                            if new_fails >= 3:
                                # 3 consecutive failures → likely delisted, mark inactive
                                log.warning("[MasterSync] %s failed %d consecutive syncs — marking INACTIVE (likely delisted)", sym, new_fails)
                                symbols_data.append({
                                    "symbol": sym,
                                    "company_name": sym,
                                    "market_cap": 0,
                                    "market_cap_bucket": "Unknown Cap",
                                    "sector": "",
                                    "industry": "",
                                    "is_active": False,
                                    "instrument_type": "EQ",
                                    "exchange": "NSE",
                                    "sync_fail_count": new_fails,
                                })
                            else:
                                # Still under threshold, keep active but record failure
                                symbols_data.append({
                                    "symbol": sym,
                                    "company_name": sym,
                                    "market_cap": 0,
                                    "market_cap_bucket": "Unknown Cap",
                                    "sector": "",
                                    "industry": "",
                                    "is_active": True,
                                    "instrument_type": "EQ",
                                    "exchange": "NSE",
                                    "sync_fail_count": new_fails,
                                })
                            synced += 1
                    except Exception as exc:
                        log.debug("[MasterSync] Metadata fetch failed for %s: %s", sym, exc)
                        failed += 1

                # Save batch immediately (resume support)
                if symbols_data:
                    db.upsert_universe_catalog(symbols_data)

                # Rate limiting for yfinance
                time.sleep(1)

                # Progress logging
                if (i + batch_size) % 100 == 0:
                    log.info("[MasterSync] Progress: %d/%d synced in round %d, %d failed total",
                             synced, round_count, batch_round, failed)

            # Log coverage after each round
            try:
                cov = db.execute_db(
                    "SELECT COUNT(*) as c FROM universe_catalog WHERE market_cap > 0",
                    fetch="one"
                )
                mcap_count = cov["c"] if cov else 0
                log.info("[MasterSync] PHASE2_BATCH_COMPLETE round=%d synced=%d failed=%d mcap_populated=%d",
                         batch_round, synced, failed, mcap_count)
            except Exception:
                log.info("[MasterSync] PHASE2_BATCH_COMPLETE round=%d synced=%d failed=%d",
                         batch_round, synced, failed)

        duration = time.time() - start_time
        log.info("[MASTER_SYNC_COMPLETED] %d synced, %d failed, %d rounds, %.1f seconds",
                 synced, failed, batch_round, duration)

        db.set_meta("master_sync_status", "completed")
        db.set_meta("master_sync_last_completed", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        db.set_meta("master_sync_synced_count", str(synced))
        db.set_meta("master_sync_failed_count", str(failed))
        db.set_meta("master_sync_skipped_provider_unavailable", str(skipped_provider_unavailable))
        db.set_meta("master_sync_duration_s", str(round(duration)))
        db.set_meta("master_sync_stale_count", str(total_stale_processed))
        db.log_scan_event(scan_id, "MASTER_SYNC_COMPLETED",
                          f"synced={synced} failed={failed} skipped={skipped_provider_unavailable} rounds={batch_round} "
                          f"stale={total_stale_processed} duration={round(duration)}s")

        return {"synced": synced, "failed": failed, "duration_s": round(duration)}

    except Exception as exc:
        log.error("[MASTER_SYNC_FAILED] %s", exc, exc_info=True)
        db.set_meta("master_sync_status", "failed")
        db.set_meta("master_sync_error", str(exc))
        db.log_scan_event(scan_id, "MASTER_SYNC_FAILED", str(exc))
        raise


def _get_stale_symbols(interval_days: int, max_batch: int) -> list:
    """Get symbols from universe_catalog where last_synced_at is NULL or older
    than interval_days. Returns at most max_batch symbols, prioritizing
    never-synced symbols first, then oldest-synced.
    """
    import db

    # Priority 1: Never-synced symbols (last_synced_at IS NULL)
    never_synced = db.execute_db(
        """SELECT symbol FROM universe_catalog
           WHERE is_active = TRUE AND last_synced_at IS NULL
           ORDER BY symbol LIMIT ?""",
        (max_batch,), fetch="all"
    ) or []

    result = [r.get("symbol") for r in never_synced if r.get("symbol")]

    if len(result) >= max_batch:
        return result[:max_batch]

    # Priority 2: Oldest-synced symbols
    remaining = max_batch - len(result)
    threshold = (datetime.now() - timedelta(days=interval_days)).strftime("%Y-%m-%d %H:%M:%S")

    oldest = db.execute_db(
        """SELECT symbol FROM universe_catalog
           WHERE is_active = TRUE AND last_synced_at IS NOT NULL
             AND last_synced_at < ?
           ORDER BY last_synced_at ASC LIMIT ?""",
        (threshold, remaining), fetch="all"
    ) or []

    result.extend(r.get("symbol") for r in oldest if r.get("symbol"))
    return result


def _get_sync_fail_count(symbol: str) -> int:
    """Get the current consecutive sync failure count for a symbol."""
    import db
    row = db.execute_db(
        "SELECT sync_fail_count FROM universe_catalog WHERE symbol=?",
        (symbol,), fetch="one"
    )
    if row and row.get("sync_fail_count") is not None:
        return int(row["sync_fail_count"])
    return 0


def _load_nse_symbols() -> list[str]:
    """Load all NSE EQ symbols from angel_tokens.json."""
    if TOKEN_FILE.exists():
        try:
            tokens = json.loads(TOKEN_FILE.read_text())
            # angel_tokens.json is {symbol: token} mapping
            return sorted(tokens.keys())
        except Exception as exc:
            log.warning("[MasterSync] Failed to load angel_tokens.json: %s", exc)

    # Fallback: try to refresh
    try:
        import live_feed
        live_feed.refresh_token_map()
        if TOKEN_FILE.exists():
            tokens = json.loads(TOKEN_FILE.read_text())
            return sorted(tokens.keys())
    except Exception:
        pass

    return []


def _fetch_symbol_metadata(symbol: str) -> dict:
    """Fetch metadata for a single symbol from yfinance.
    Returns dict with keys: symbol, company_name, market_cap, market_cap_bucket,
                            sector, industry, is_active, instrument_type, exchange, price
    """
    from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session

    if not yf_is_available():
        return None

    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS", session=get_yf_session())
        info = ticker.info

        if not info or info.get("regularMarketPrice") is None:
            return None

        market_cap = info.get("marketCap", 0) or 0
        market_cap_cr = market_cap / 1e7  # Convert to Crores

        # Determine market cap bucket
        bucket = _classify_market_cap(market_cap_cr)

        result = {
            "symbol": symbol,
            "company_name": info.get("longName") or info.get("shortName") or symbol,
            "market_cap": market_cap_cr,
            "market_cap_bucket": bucket,
            "sector": info.get("sector") or "",
            "industry": info.get("industry") or "",
            "is_active": True,
            "instrument_type": _detect_instrument_type(info),
            "exchange": "NSE",
            "price": info.get("regularMarketPrice") or info.get("currentPrice") or 0,
        }

        yf_record_success()
        return result

    except Exception as exc:
        log.debug("[MasterSync] yfinance failed for %s: %s", symbol, exc)
        yf_record_failure()
        return None


def _classify_market_cap(mcap_cr: float) -> str:
    """Classify market cap into buckets."""
    if mcap_cr >= 50000:
        return "Blue Chip"
    elif mcap_cr >= 20000:
        return "Large Cap"
    elif mcap_cr >= 5000:
        return "Mid Cap"
    elif mcap_cr >= 1000:
        return "Small Cap"
    elif mcap_cr > 0:
        return "Micro Cap"
    return "Unknown Cap"


def _detect_instrument_type(info: dict) -> str:
    """Detect if the instrument is an ETF, mutual fund, etc."""
    quote_type = (info.get("quoteType") or "").upper()
    if quote_type == "ETF":
        return "ETF"
    if quote_type == "MUTUALFUND":
        return "MF"
    return "EQ"
