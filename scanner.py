"""
Stock scanner — Phase 1 (Angel One) + Phase 2 (jugaad_data fallback).
DB is single source of truth. No shared mutable state for results.

Upgraded: Pre-scan intelligence warmup (GDELT+FinBERT, world markets, RRG, Forex Factory)
"""

import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


from stocks import STOCK_UNIVERSE
from config import MAX_WORKERS, BATCH_SIZE, BATCH_DELAY, DATA_LOOKBACK_DAYS, CACHE_TTL_HOURS
from analyzer import (
    fetch_and_analyze, get_nifty50_benchmark,
    apply_sector_strength, generate_ai_summary, reset_delivery_state,
)
import live_feed
import db

log = logging.getLogger("screener")


# ===================================================================
#  SCAN STATE — thread-safe, holds only progress metadata
# ===================================================================

class ScanState:
    def __init__(self):
        self._lock = threading.Lock()
        self._scanning = False
        self._progress = 0
        self._total = 0

    def start(self, total: int):
        with self._lock:
            self._scanning = True
            self._progress = 0
            self._total = total

    def finish(self):
        with self._lock:
            self._scanning = False

    def set_progress(self, value: int):
        with self._lock:
            self._progress = min(value, self._total)

    @property
    def is_scanning(self) -> bool:
        with self._lock:
            return self._scanning

    def status(self) -> dict:
        with self._lock:
            return {
                "scanning": self._scanning,
                "progress": self._progress,
                "total": self._total,
            }


scan_state = ScanState()


# ===================================================================
#  CACHE CHECK
# ===================================================================

def has_valid_cache() -> bool:
    """Check if DB has valid (non-stale) scan results."""
    db.init_db()
    if db.get_result_count() == 0:
        return False
    timestamp = db.get_meta("timestamp")
    if timestamp:
        try:
            cached_time = datetime.fromisoformat(timestamp)
            # Handle naive vs aware datetime comparison
            if cached_time.tzinfo is None:
                age_hours = (now_ist().replace(tzinfo=None) - cached_time).total_seconds() / 3600
            else:
                age_hours = (now_ist() - cached_time).total_seconds() / 3600
            if age_hours > CACHE_TTL_HOURS:
                return False
        except (ValueError, TypeError):
            pass
    return True


# ===================================================================
#  FULL SCAN — writes directly to DB
# ===================================================================

def run_full_scan():
    """Run full stock scan. Writes results to DB incrementally."""
    custom = [s["symbol"] for s in db.get_custom_stocks()]
    all_symbols = list(STOCK_UNIVERSE) + [s for s in custom if s not in STOCK_UNIVERSE]
    total = len(all_symbols)

    scan_state.start(total)
    log.info("Scan: %d stocks...", total)
    start_time = time.time()

    try:
        # ── PRE-SCAN INTELLIGENCE WARMUP ──────────────────────────
        # Warms: GDELT+FinBERT article cache, world markets,
        # sector rotation (RRG), Forex Factory macro events.
        # All results cached globally — O(1) per-stock lookup.
        try:
            from intelligence import warmup_all
            warmup_all(set(all_symbols))
        except Exception as exc:
            log.warning("Intelligence warmup failed (continuing): %s", exc)

        # Reset delivery enrichment flag
        reset_delivery_state()

        # Benchmark
        nifty_1m, regime = get_nifty50_benchmark()
        db.set_meta("nifty50_1m", nifty_1m)
        db.set_meta("market_regime", regime)
        log.info("Nifty 1M: %+.2f%% | Regime: %s", nifty_1m, regime.upper())

        results = []
        failed_symbols = []
        scored_set = set()

        # ── PHASE 1: Angel One historical (primary — fresh data) ──
        log.info("Phase 1: Angel One (%d stocks)...", total)
        for i, sym in enumerate(all_symbols):
            try:
                df = live_feed.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
                if df is not None and not df.empty and len(df) >= 50:
                    r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df)
                    if r:
                        results.append(r)
                        scored_set.add(sym)
                        db.save_results([r])  # immediate DB write
                else:
                    failed_symbols.append(sym)
            except Exception:
                failed_symbols.append(sym)

            scan_state.set_progress(i + 1)

            if (i + 1) % 50 == 0:
                log.info("Phase 1: %d/%d done, %d scored", i + 1, total, len(results))

        log.info("Phase 1 done: %d scored, %d failed", len(results), len(failed_symbols))

        # ── PHASE 2: jugaad_data fallback (has delivery %) ──
        if failed_symbols:
            log.info("Phase 2: jugaad_data fallback (%d stocks)...", len(failed_symbols))
            jugaad_scored = 0
            for batch_start in range(0, len(failed_symbols), BATCH_SIZE):
                batch = failed_symbols[batch_start:batch_start + BATCH_SIZE]
                if batch_start > 0:
                    time.sleep(BATCH_DELAY)

                batch_results = []
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(fetch_and_analyze, sym, nifty_1m, regime): sym
                        for sym in batch
                    }
                    for future in as_completed(futures):
                        sym = futures[future]
                        try:
                            r = future.result()
                            if r:
                                results.append(r)
                                batch_results.append(r)
                                scored_set.add(sym)
                                jugaad_scored += 1
                        except Exception:
                            pass

                # Save batch to DB
                if batch_results:
                    db.save_results(batch_results)

                batch_num = batch_start // BATCH_SIZE + 1
                log.info("Phase 2 batch %d: +%d scored", batch_num, jugaad_scored)

                # First batch 0 → jugaad blocked, skip
                if batch_num >= 1 and jugaad_scored == 0:
                    log.warning("Phase 2: jugaad_data blocked — skipping")
                    break

            log.info("Phase 2 done: +%d from jugaad_data", jugaad_scored)

        # ── POST-SCAN MARKETAUX ENRICHMENT FOR TOP 30 ─────────────
        log.info("Top 30 candidates MarketAux enrichment check...")
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        top30 = results[:30]
        enriched_count = 0
        
        for r in top30:
            sym = r["symbol"]
            if not r.get("marketaux_queried", False):
                log.info("Enriching Top 30 Candidate: %s with MarketAux", sym)
                try:
                    df = live_feed.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
                    if df is not None and not df.empty:
                        new_r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df, query_marketaux=True)
                        if new_r:
                            # Replace in results list
                            for idx, item in enumerate(results):
                                if item["symbol"] == sym:
                                    results[idx] = new_r
                                    break
                            db.save_results([new_r])
                            enriched_count += 1
                except Exception as e:
                    log.warning("MarketAux enrichment failed for %s: %s", sym, e)
                        
        log.info("MarketAux enrichment complete: %d stocks enriched", enriched_count)

        # ── FINALIZE ──
        # Apply sector strength (modifies results in-place)
        try:
            heatmap = apply_sector_strength(results)
            db.set_meta("heatmap", heatmap)
        except Exception as exc:
            log.warning("Heatmap failed: %s", exc)

        try:
            summary = generate_ai_summary(results, regime)
            db.set_meta("summary", summary)
        except Exception as exc:
            log.warning("Summary failed: %s", exc)

        # Final save — all results with sector strength applied
        db.save_results(results)
        db.set_meta("last_scan", now_ist().strftime("%Y-%m-%d %H:%M IST"))
        db.set_meta("timestamp", now_ist().isoformat())

        elapsed = time.time() - start_time
        hc_count = sum(1 for r in results if r.get("high_conviction"))
        log.info("Done in %.0fs! %d scored, %d HC", elapsed, len(results), hc_count)

        # Subscribe all to live feed
        live_feed.subscribe([r["symbol"] for r in results])

    except Exception as exc:
        log.error("Scan failed: %s", exc)
    finally:
        scan_state.finish()
