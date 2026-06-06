"""
Stock scanner -- Phase 1 (Angel One) + Phase 2 (jugaad_data fallback).
DB is single source of truth. No shared mutable state for results.

Phase 4: Event-driven architecture:
  1. refresh_news_pipeline() detects news spikes + NSE announcements
  2. _shortlist_for_deep_scan() picks candidates with hard cap
  3. run_full_scan() runs fast scan, then deep scan on shortlisted
"""

import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from metrics.timer import timed, _record as record_timing

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


from universe import get_fast_scan_universe, save_active_universe
from config import MAX_WORKERS, BATCH_SIZE, BATCH_DELAY, DATA_LOOKBACK_DAYS, CACHE_TTL_HOURS
from analyzer import (
    fetch_and_analyze, get_nifty50_benchmark,
    apply_sector_strength, generate_ai_summary, reset_delivery_state,
)
from intelligence.news_gdelt_finbert import build_article_cache, _article_cache, _cache_lock
from intelligence.news_sentiment import _fetch_nse_announcements, get_nse_affected_symbols
import live_feed
import db

log = logging.getLogger("screener")


# ===================================================================
#  SCAN STATE — DB-backed (Phase 6), imported from db.py
# ===================================================================
# The ScanState class now lives in db.py with full DB persistence.
# scanner.py just imports the singleton.

from db import scan_state


# ===================================================================
#  MARKETAUX BACKGROUND WORKER (Phase 8)
# ===================================================================
import queue as _queue_mod

_marketaux_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=200)
_marketaux_thread: threading.Thread | None = None
_marketaux_overflow_count = 0


def _marketaux_worker():
    """Background daemon: pulls symbols from queue, enriches with MarketAux."""
    while True:
        try:
            sym = _marketaux_queue.get(timeout=5)
        except _queue_mod.Empty:
            continue
        try:
            nifty_1m = live_feed.get_nifty_1m()
            regime = db.get_meta("market_regime", "unknown")
            df = live_feed.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
            if df is not None and not df.empty:
                new_r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df, query_marketaux=True, scan_mode="deep")
                if new_r:
                    db.save_results([new_r])
                    log.info("MarketAux BG: Enriched %s (score=%s)", sym, new_r.get("score", "?"))
        except Exception as exc:
            log.warning("MarketAux BG: Failed for %s: %s", sym, exc)
        finally:
            _marketaux_queue.task_done()


def enqueue_marketaux(symbols: list):
    """Non-blocking enqueue for MarketAux enrichment. Drops gracefully if full."""
    global _marketaux_overflow_count
    for sym in symbols:
        try:
            _marketaux_queue.put_nowait(sym)
        except _queue_mod.Full:
            _marketaux_overflow_count += 1
            log.warning("MarketAux queue full (overflow #%d), dropping: %s", _marketaux_overflow_count, sym)


def start_marketaux_worker():
    """Start background MarketAux worker thread (idempotent)."""
    global _marketaux_thread
    if _marketaux_thread is not None and _marketaux_thread.is_alive():
        return
    _marketaux_thread = threading.Thread(target=_marketaux_worker, daemon=True, name="marketaux-bg")
    _marketaux_thread.start()
    log.info("MarketAux background worker started")


def get_marketaux_queue_depth() -> int:
    return _marketaux_queue.qsize()


def get_marketaux_overflow_count() -> int:
    return _marketaux_overflow_count


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
#  EVENT-DRIVEN: News pipeline + shortlisting (Phase 4)
# ===================================================================

_prev_article_counts: dict = {}  # symbol -> article count from previous refresh


@timed("news_pipeline")
def refresh_news_pipeline(all_symbols: set) -> dict:
    """
    Refresh all news sources BEFORE scan. Detects two event signals:
      1. NSE corporate announcements (1 HTTP call)
      2. GDELT news volume spikes (1 HTTP call + FinBERT scoring)

    Returns {"spikes": set, "announcements": set}
    """
    global _prev_article_counts

    # 1. NSE announcements
    try:
        _fetch_nse_announcements()
    except Exception as exc:
        log.warning("NSE announcements fetch failed: %s", exc)
    nse_affected = get_nse_affected_symbols()

    # 2. Rebuild GDELT article cache and detect spikes
    # Snapshot previous counts before rebuild
    with _cache_lock:
        prev_counts = {sym: len(data.get("articles", [])) for sym, data in _article_cache.items()}

    try:
        build_article_cache(all_symbols)
    except Exception as exc:
        log.warning("GDELT cache rebuild failed: %s", exc)

    # Compare new vs previous article counts to find spikes
    spikes = set()
    with _cache_lock:
        for sym, data in _article_cache.items():
            new_count = len(data.get("articles", []))
            old_count = prev_counts.get(sym, 0)
            # Spike: >2x previous count AND at least 3 articles
            if new_count >= 3 and old_count > 0 and new_count > old_count * 2:
                spikes.add(sym)
            # Also flag if GDELT spike ratio > 2
            if data.get("spike", 1.0) > 2.0:
                spikes.add(sym)

    _prev_article_counts = {
        sym: len(data.get("articles", []))
        for sym, data in _article_cache.items()
    }

    log.info(
        "News pipeline: %d NSE announcements, %d GDELT spikes",
        len(nse_affected), len(spikes),
    )

    # Phase 7: Flag event-driven symbols for deep scan
    for sym in spikes:
        db.mark_deep_scan_needed(sym, reason="news_spike")
    for sym in nse_affected:
        db.mark_deep_scan_needed(sym, reason="corp_event")

    return {"spikes": spikes, "announcements": nse_affected}


def _shortlist_for_deep_scan(
    fast_results: list,
    event_signals: dict,
    hard_cap: int = 100,
    soft_target: int = 50,
) -> list:
    """
    Build shortlist of candidates for deep scan from fast scan results + event signals.

    Tiered selection:
      Tier 1: Event-driven (NSE announcements | GDELT spikes) -- highest priority
      Tier 2: Breakouts + score>=60 + vol_ratio>=2.0
      Tier 3: Score>=40 -- fill to soft_target only

    Hard cap enforced: never returns more than hard_cap candidates.
    """
    spikes = event_signals.get("spikes", set())
    announcements = event_signals.get("announcements", set())
    # Phase 7: Also include DB-flagged symbols needing deep scan
    db_flagged = set(db.get_symbols_needing_deep_scan(limit=hard_cap))
    event_syms = spikes | announcements | db_flagged

    candidates = []
    seen = set()

    # Tier 1: Event-driven + DB-flagged
    for r in fast_results:
        sym = r.get("symbol", "")
        if sym in event_syms and sym not in seen:
            candidates.append(sym)
            seen.add(sym)
            if len(candidates) >= hard_cap:
                break
    # Also add DB-flagged symbols not in fast_results
    if len(candidates) < hard_cap:
        for sym in db_flagged - seen:
            candidates.append(sym)
            seen.add(sym)
            if len(candidates) >= hard_cap:
                break

    # Tier 2: Breakouts + high score + high volume
    if len(candidates) < hard_cap:
        for r in sorted(fast_results, key=lambda x: x.get("score", 0), reverse=True):
            sym = r.get("symbol", "")
            if sym in seen:
                continue
            score = r.get("score", 0)
            vol = r.get("volume_ratio", 1.0) or 1.0
            is_breakout = r.get("is_breakout", False)
            if (is_breakout and score >= 60) or (score >= 60 and vol >= 2.0):
                candidates.append(sym)
                seen.add(sym)
                if len(candidates) >= hard_cap:
                    break

    # Tier 3: Score >= 40 -- fill to soft_target only
    if len(candidates) < soft_target:
        for r in sorted(fast_results, key=lambda x: x.get("score", 0), reverse=True):
            sym = r.get("symbol", "")
            if sym in seen:
                continue
            if r.get("score", 0) >= 40:
                candidates.append(sym)
                seen.add(sym)
                if len(candidates) >= soft_target:
                    break

    # Hard cap enforcement -- no exceptions
    final = candidates[:hard_cap]
    log.info(
        "Deep scan shortlist: %d candidates (hard_cap=%d) "
        "[T1_events=%d, T2_breakout=%d, T3_score=%d]",
        len(final), hard_cap,
        len(event_syms & seen),
        sum(1 for s in seen if s not in event_syms),
        max(0, len(final) - len(event_syms & seen)),
    )
    return final


# ===================================================================
#  FULL SCAN -- writes directly to DB
# ===================================================================

@timed("full_scan")
def run_full_scan():
    """Run full stock scan. Writes results to DB incrementally."""
    # Phase 5: Flush any deferred writes from previous cycle
    try:
        flushed = db.flush_deferred_writes()
        if flushed:
            log.info("Flushed %d deferred writes from DLQ", flushed)
    except Exception as exc:
        log.warning("DLQ flush failed: %s", exc)

    try:
        live_feed.reset_login_circuit_breaker()
    except Exception:
        pass
        
    # Build universe: curated active set + any custom stocks not already included
    curated_symbols = get_fast_scan_universe()
    custom = [s["symbol"] for s in db.get_custom_stocks()]
    curated_set = set(curated_symbols)
    all_symbols = curated_symbols + [s for s in custom if s not in curated_set]
    total = len(all_symbols)

    # Persist the resolved universe for debugging / transparency
    try:
        save_active_universe(all_symbols)
    except Exception as exc:
        log.debug("save_active_universe failed (non-fatal): %s", exc)

    scan_state.start(total, mode="manual")
    log.info("Scan: %d stocks...", total)
    start_time = time.monotonic()

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
                else:
                    failed_symbols.append(sym)
            except Exception:
                failed_symbols.append(sym)

            scan_state.set_progress(i + 1)

            # Phase 6: Check for cancellation
            if scan_state.cancel_requested:
                log.warning("Scan cancelled by user at %d/%d", i + 1, total)
                break

            if (i + 1) % 50 == 0:
                log.info("Phase 1: %d/%d done, %d scored", i + 1, total, len(results))

        scan_state.update(phase="phase1_done")
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

        # ── POST-SCAN MARKETAUX: ENQUEUE TO BACKGROUND WORKER (Phase 8) ──
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        top30_syms = [
            r["symbol"] for r in results[:30]
            if not r.get("marketaux_queried", False)
        ]
        if top30_syms:
            enqueue_marketaux(top30_syms)
            log.info("MarketAux: Enqueued %d top symbols for background enrichment", len(top30_syms))

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

        elapsed = time.monotonic() - start_time
        hc_count = sum(1 for r in results if r.get("high_conviction"))
        log.info("Done in %.0fs! %d scored, %d HC", elapsed, len(results), hc_count)

        # Persist timing metrics baseline
        try:
            import json
            from metrics.timer import get_report
            db.set_meta("perf_baseline", json.dumps({
                "captured_at": datetime.now().isoformat(),
                "scan_duration_min": round(elapsed / 60, 2),
                "symbol_count": len(results),
                "operations": get_report()
            }))
            log.info("Timing baseline persisted to scan_meta")
        except Exception as exc:
            log.warning("Failed to persist timing baseline: %s", exc)

        # Subscribe all to live feed
        live_feed.subscribe([r["symbol"] for r in results])

    except Exception as exc:
        log.error("Scan failed: %s", exc)
        scan_state.complete(success=False, error_message=str(exc)[:500])
        return
    finally:
        # Ensure state is reset even on unexpected errors
        if scan_state.is_scanning:
            scan_state.complete(success=True)
