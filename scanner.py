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
#  SCAN STATE — DB-backed (Phase 6 + Phase 0A Hardening)
# ===================================================================
# Phase 0A: ScanState class is now a backward-compat wrapper.
# scanner.py uses the new module-level functions directly.

from db import (
    scan_state, acquire_scan_lock, transition_scan_state,
    update_scan_progress, is_scan_active, get_scan_cancel_requested,
    save_state_transition,
)
from scan_context import ScanContext
from events import ACTOR_SYSTEM, ACTOR_USER, ACTOR_AUTO_SCAN

# Phase 0B: Graceful shutdown event — shared across all daemon threads
_shutdown_event = threading.Event()


# ===================================================================
#  MARKETAUX BACKGROUND WORKER (Phase 8)
# ===================================================================
import queue as _queue_mod

_marketaux_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=200)
_marketaux_thread: threading.Thread | None = None
_marketaux_overflow_count = 0


def _marketaux_worker():
    """Background daemon: pulls symbols from queue, enriches with MarketAux.
    Phase 0B: Checks _shutdown_event for graceful termination.
    """
    while not _shutdown_event.is_set():
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
def run_full_scan(context: ScanContext = None):
    """Run full stock scan. Writes results to DB incrementally.

    Phase 0A: Uses ScanContext for execution ownership.
    Phase 0B: Guaranteed terminal state via finally block.
    Phase 1: Full context propagation (correlation_id, versions, config_snapshot).
    """
    # Phase 1: Create context if not provided (auto-scan / legacy callers)
    if context is None:
        context = ScanContext.create(
            trigger_source="auto",
            user_id="system",
            mode="manual",
        )

    scan_id = context.scan_id
    correlation_id = context.correlation_id

    # Phase 5: Flush any deferred writes from previous cycle
    try:
        flushed = db.flush_deferred_writes()
        if flushed:
            log.info("[%s] Flushed %d deferred writes from DLQ", correlation_id[:12], flushed)
    except Exception as exc:
        log.warning("[%s] DLQ flush failed: %s", correlation_id[:12], exc)

    try:
        live_feed.reset_login_circuit_breaker()
    except Exception:
        pass

    # Phase 6, Section 39: Configuration drift check at scan ingress
    try:
        from config import check_config_drift
        from events import CONFIG_DRIFT_DETECTED
        _drift = check_config_drift()
        if _drift:
            log.warning(
                "[%s] CONFIG DRIFT DETECTED — %d variable(s) changed: %s",
                correlation_id[:12], len(_drift), list(_drift.keys())
            )
            # Persist drift details for audit trail
            import json as _json
            db.set_meta("config_drift", _json.dumps({
                "scan_id": scan_id,
                "drift": {k: {dk: str(dv) for dk, dv in v.items()} for k, v in _drift.items()},
                "detected_at": datetime.now(IST).isoformat(),
            }))
            # Emit event via state transition audit trail
            save_state_transition(
                scan_id, "running", "running",
                reason=f"config_drift: {list(_drift.keys())}",
                actor=ACTOR_SYSTEM,
                correlation_id=correlation_id,
            )
        else:
            log.info("[%s] Config drift check passed — no changes from baseline", correlation_id[:12])
    except Exception as exc:
        log.debug("[%s] Config drift check failed (non-fatal): %s", correlation_id[:12], exc)

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

    # Phase 0A: Atomic lock acquisition via ScanContext
    lock_acquired = scan_state.start(total, mode=context.trigger_source, context=context)
    if lock_acquired is None:
        # Lock not acquired — another scan is running (Section 32: TOCTOU prevention)
        log.warning("[%s] Scan rejected — another scan is already active", correlation_id[:12])
        return

    db.clear_meta_cache()  # Phase 1: ensure fresh metadata during scan
    log.info("[%s] Scan: %d stocks... (scan_id=%s)", correlation_id[:12], total, scan_id[:20])
    start_time = time.monotonic()

    # Phase 0B: Track whether we reached a terminal state
    _reached_terminal = False

    try:
        # ── PRE-SCAN INTELLIGENCE WARMUP ────────────────────────────
        # Warms: GDELT+FinBERT article cache, world markets,
        # sector rotation (RRG), Forex Factory macro events.
        # All results cached globally — O(1) per-stock lookup.
        try:
            from intelligence import warmup_all
            warmup_all(set(all_symbols))
        except Exception as exc:
            log.warning("[%s] Intelligence warmup failed (continuing): %s", correlation_id[:12], exc)

        # Reset delivery enrichment flag
        reset_delivery_state()

        # Benchmark
        nifty_1m, regime = get_nifty50_benchmark()
        db.set_meta("nifty50_1m", nifty_1m)
        db.set_meta("market_regime", regime)
        log.info("[%s] Nifty 1M: %+.2f%% | Regime: %s", correlation_id[:12], nifty_1m, regime.upper())

        results = []
        failed_symbols = []
        scored_set = set()

        # ── PHASE 1: Angel One historical (primary — fresh data) ──
        log.info("[%s] Phase 1: Angel One (%d stocks)...", correlation_id[:12], total)

        # Phase 3: Log phase transition
        save_state_transition(scan_id, "running", "running",
                              reason="phase1_started", actor=ACTOR_SYSTEM,
                              correlation_id=correlation_id)

        # Phase 6: Chunk Execution Architecture
        import universe
        chunks = universe.get_universe_chunks(all_symbols)
        
        global_i = 0
        for chunk_name, chunk_symbols in chunks:
            if not chunk_symbols:
                continue
                
            chunk_run_id = db.start_chunk_run(scan_id, chunk_name, len(chunk_symbols))
            chunk_processed = 0
            chunk_failed = 0
            
            log.info("[%s] Starting chunk: %s (%d symbols)", correlation_id[:12], chunk_name, len(chunk_symbols))
            
            for sym in chunk_symbols:
                try:
                    df = live_feed.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
                    if df is not None and not df.empty and len(df) >= 50:
                        r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df)
                        if r:
                            results.append(r)
                            scored_set.add(sym)
                    else:
                        failed_symbols.append(sym)
                        chunk_failed += 1
                except Exception:
                    failed_symbols.append(sym)
                    chunk_failed += 1
                
                chunk_processed += 1
                global_i += 1
                scan_state.set_progress(global_i)
                
                # Phase 6: Check for cancellation
                if get_scan_cancel_requested():
                    log.warning("[%s] Scan cancelled by user at %d/%d", correlation_id[:12], global_i, total)
                    db.end_chunk_run(chunk_run_id, "CANCELLED", chunk_processed, "User cancelled scan")
                    transition_scan_state(
                        scan_id=scan_id, from_status="running", to_status="cancelled",
                        reason="user_cancelled", actor=ACTOR_USER,
                        correlation_id=correlation_id,
                    )
                    _reached_terminal = True
                    return
                
                if global_i % 50 == 0:
                    log.info("[%s] Phase 1: %d/%d done, %d scored", correlation_id[:12], global_i, total, len(results))
            
            # End chunk run
            db.end_chunk_run(chunk_run_id, "COMPLETED", chunk_processed, f"{chunk_failed} failed")
            
            # Mandatory cooling delay between chunks
            time.sleep(3)

        scan_state.update(phase="phase1_done")
        log.info("[%s] Phase 1 done: %d scored, %d failed", correlation_id[:12], len(results), len(failed_symbols))

        # Phase 4, Section 37: Data quality gate — abort if too many symbols failed
        _degraded_data = False
        if total > 0:
            _fail_pct = len(failed_symbols) / total
            if _fail_pct > 0.05 and len(failed_symbols) > 5:
                # Check if this will improve in Phase 2 (jugaad_data fallback)
                # Only abort if we're missing critical mass (>5% AND more than 5 symbols)
                log.warning(
                    "[%s] Data quality check: %.1f%% symbols failed Phase 1 (%d/%d). "
                    "Will attempt jugaad_data fallback before final quality decision.",
                    correlation_id[:12], _fail_pct * 100, len(failed_symbols), total
                )

        # ── PHASE 2: jugaad_data fallback (has delivery %) ──
        if failed_symbols:
            log.info("[%s] Phase 2: jugaad_data fallback (%d stocks)...", correlation_id[:12], len(failed_symbols))
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
                log.info("[%s] Phase 2 batch %d: +%d scored", correlation_id[:12], batch_num, jugaad_scored)

                # First batch 0 → jugaad blocked, skip
                if batch_num >= 1 and jugaad_scored == 0:
                    log.warning("[%s] Phase 2: jugaad_data blocked — skipping", correlation_id[:12])
                    break

            log.info("[%s] Phase 2 done: +%d from jugaad_data", correlation_id[:12], jugaad_scored)

        # Phase 4, Section 37: Final data quality gate (post-fallback)
        _final_failed = total - len(results)
        if total > 0 and _final_failed > 5:
            _final_fail_pct = _final_failed / total
            if _final_fail_pct > 0.05:
                # Hard abort — too many symbols have no price data at all
                _abort_reason = (
                    f"data_quality_abort: {_final_failed}/{total} symbols "
                    f"({_final_fail_pct:.1%}) failed both Angel One and yfinance"
                )
                log.error("[%s] %s", correlation_id[:12], _abort_reason)
                transition_scan_state(
                    scan_id=scan_id, from_status="running", to_status="failed",
                    reason=_abort_reason, actor=ACTOR_SYSTEM,
                    correlation_id=correlation_id,
                    error_message=_abort_reason,
                )
                _reached_terminal = True
                return

        # Phase 4: Check if non-critical feeds degraded (GDELT, MarketAux)
        # Intelligence warmup failures are soft — we continue but flag
        try:
            _warmup_meta = db.get_meta("intelligence_warmup_status", {})
            if isinstance(_warmup_meta, dict) and _warmup_meta.get("gdelt_failed"):
                _degraded_data = True
                log.warning("[%s] Non-critical feed degraded: GDELT unavailable", correlation_id[:12])
        except Exception:
            pass

        # Persist degraded_data flag to scan_runs
        if _degraded_data:
            try:
                db.execute_db(
                    "UPDATE scan_runs SET degraded_data=? WHERE scan_id=?",
                    (True, scan_id)
                )
                log.warning("[%s] Scan flagged as degraded_data=True", correlation_id[:12])
            except Exception as exc:
                log.debug("[%s] Failed to set degraded_data: %s", correlation_id[:12], exc)

        # ── POST-SCAN MARKETAUX: ENQUEUE TO BACKGROUND WORKER (Phase 8) ──
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        top30_syms = [
            r["symbol"] for r in results[:30]
            if not r.get("marketaux_queried", False)
        ]
        if top30_syms:
            enqueue_marketaux(top30_syms)
            log.info("[%s] MarketAux: Enqueued %d top symbols for background enrichment", correlation_id[:12], len(top30_syms))

        # ── FINALIZE ──
        # Apply sector strength (modifies results in-place)
        try:
            heatmap = apply_sector_strength(results)
            db.set_meta("heatmap", heatmap)
        except Exception as exc:
            log.warning("[%s] Heatmap failed: %s", correlation_id[:12], exc)

        try:
            summary = generate_ai_summary(results, regime)
            db.set_meta("summary", summary)
        except Exception as exc:
            log.warning("[%s] Summary failed: %s", correlation_id[:12], exc)

        # Final save — all results with sector strength applied
        db.save_results(results)
        
        # Phase 5: Snapshot Governance (Immutable Research Freeze)
        for r in results:
            if r.get("high_conviction") or r.get("score", 0) >= 65:
                try:
                    db.save_research_snapshot_v2(r.get("symbol"), r, scan_context)
                except Exception as exc:
                    log.warning("[%s] Failed to save research snapshot for %s: %s", correlation_id[:12], r.get("symbol"), exc)
                    
        db.set_meta("last_scan", now_ist().strftime("%Y-%m-%d %H:%M IST"))
        db.set_meta("timestamp", now_ist().isoformat())

        elapsed = time.monotonic() - start_time
        hc_count = sum(1 for r in results if r.get("high_conviction"))
        log.info("[%s] Done in %.0fs! %d scored, %d HC", correlation_id[:12], elapsed, len(results), hc_count)

        # Phase F: Structured scan performance telemetry
        _rate = round(len(results) / elapsed, 2) if elapsed > 0 else 0
        log.info(
            "[SCAN PERF] scan_id=%s | correlation=%s | symbols=%d | results_saved=%d | failed=%d | "
            "duration=%dms | rate=%.2f/sec",
            scan_id[:20], correlation_id[:12], total, len(results), total - len(results),
            round(elapsed * 1000), _rate
        )

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
            log.info("[%s] Timing baseline persisted to scan_meta", correlation_id[:12])
        except Exception as exc:
            log.warning("[%s] Failed to persist timing baseline: %s", correlation_id[:12], exc)

        # Phase 0: Trust & Observability — audit trail
        try:
            from config import SCAN_VERSION
            _scan_start_str = datetime.fromtimestamp(start_time + time.time() - time.monotonic()).strftime("%Y-%m-%d %H:%M:%S")
            _scan_end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Score audit: one row per stock per scan
            db.save_score_audit(results, scan_id, SCAN_VERSION)

            # Scan audit: one row per scan run
            db.save_scan_audit(
                scan_id=scan_id,
                start_time=_scan_start_str,
                end_time=_scan_end_str,
                duration_ms=round(elapsed * 1000),
                stocks_scanned=total,
                stocks_succeeded=len(results),
                stocks_failed=total - len(results),
                data_source="ANGEL",  # primary source used
                scan_version=SCAN_VERSION,
                scan_mode=context.trigger_source,
            )
        except Exception as exc:
            log.warning("[%s] Phase 0: audit trail failed (non-fatal): %s", correlation_id[:12], exc)

        # ── R1 EVIDENCE COLLECTION (Append-Only, Schema-Frozen) ──────
        try:
            import csv as _csv
            from datetime import date as _obs_date
            from pathlib import Path as _Path

            _R1_DEPLOY_DATE = "2026-06-08"
            _today_str = now_ist().strftime("%Y-%m-%d")
            _obs_day = (_obs_date.today() - _obs_date.fromisoformat(_R1_DEPLOY_DATE)).days + 1
            _release = "R1.0"
            _audit_dir = _Path(__file__).parent / "release_audits"
            _audit_dir.mkdir(parents=True, exist_ok=True)

            # Scan status classification
            _fail_count = total - len(results)
            if _fail_count == 0:
                _scan_status = "SUCCESS"
            elif len(results) > total * 0.5:
                _scan_status = "PARTIAL"
            else:
                _scan_status = "FAILED"

            # Pre-compute score percentiles
            _scores = sorted([r.get("score", 0) for r in results]) if results else []
            def _pct(p):
                if not _scores: return 0
                idx = int(len(_scores) * p / 100)
                return _scores[min(idx, len(_scores) - 1)]

            _hc_count = sum(1 for r in results if r.get("high_conviction"))
            _golden_count = sum(1 for r in results if r.get("is_golden"))
            _top_sym = results[0].get("symbol", "") if results else ""
            _top_score = results[0].get("score", 0) if results else 0

            # Store scan_id for trade_outcomes.csv to reference
            db.set_meta("current_scan_id", scan_id)

            _manifest_rows = []  # (artifact_name, rows_written)

            # ── Artifact 1: daily_release1_snapshot.csv ──
            _snap_path = _audit_dir / "daily_release1_snapshot.csv"
            _snap_header = not _snap_path.exists()
            with open(_snap_path, "a", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                if _snap_header:
                    w.writerow([
                        "Date", "Scan ID", "Release Version", "Observation Day",
                        "Scan Status", "Stocks Attempted", "Stocks Successfully Analyzed",
                        "Stocks Failed", "HC Count", "Golden Count",
                        "P50", "P75", "P90", "P95", "P99",
                        "Max Score", "Top Symbol", "Top Score",
                    ])
                w.writerow([
                    _today_str, scan_id, _release, _obs_day,
                    _scan_status, total, len(results), _fail_count,
                    _hc_count, _golden_count,
                    _pct(50), _pct(75), _pct(90), _pct(95), _pct(99),
                    _scores[-1] if _scores else 0, _top_sym, _top_score,
                ])
            _manifest_rows.append(("daily_release1_snapshot.csv", 1))
            log.info("[R1 Evidence] daily_release1_snapshot.csv appended (Day %d)", _obs_day)

            # ── Artifact 2: daily_top20_snapshot.csv ──
            _top20_path = _audit_dir / "daily_top20_snapshot.csv"
            _top20_header = not _top20_path.exists()
            _ranked = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:20]
            with open(_top20_path, "a", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                if _top20_header:
                    w.writerow([
                        "Date", "Scan ID", "Release Version", "Observation Day",
                        "Rank", "Symbol", "Score", "HC", "Golden",
                        "Risk", "RR", "Sector", "Sector Rotation Score",
                    ])
                for rank, r in enumerate(_ranked, 1):
                    w.writerow([
                        _today_str, scan_id, _release, _obs_day,
                        rank, r.get("symbol", ""), r.get("score", 0),
                        1 if r.get("high_conviction") else 0,
                        1 if r.get("is_golden") else 0,
                        r.get("risk_score", 0), r.get("risk_reward", 0),
                        r.get("sector", ""), r.get("sector_rotation_score", 0),
                    ])
            _manifest_rows.append(("daily_top20_snapshot.csv", len(_ranked)))
            log.info("[R1 Evidence] daily_top20_snapshot.csv appended (%d rows)", len(_ranked))

            # ── Artifact 3: daily_open_trades_mtm.csv ──
            _mtm_path = _audit_dir / "daily_open_trades_mtm.csv"
            _mtm_header = not _mtm_path.exists()
            _open_trades = db.get_open_paper_trades()
            # Build price lookup from scan results
            _price_map = {r.get("symbol", ""): r.get("price", 0) for r in results}
            _mtm_written = 0
            with open(_mtm_path, "a", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                if _mtm_header:
                    w.writerow([
                        "Date", "Scan ID", "Release Version", "Observation Day",
                        "Date Opened", "Symbol", "Entry Price", "Current Price",
                        "Unrealized Return %", "HC Flag (Entry)", "Score (Entry)",
                    ])
                for t in _open_trades:
                    _sym = t.get("symbol", "")
                    _entry_p = t.get("entry_price", 0)
                    _curr_p = _price_map.get(_sym, _entry_p)
                    _unreal = round(((_curr_p - _entry_p) / _entry_p) * 100, 2) if _entry_p > 0 else 0
                    w.writerow([
                        _today_str, scan_id, _release, _obs_day,
                        t.get("entry_date", ""), _sym, _entry_p, _curr_p,
                        _unreal, t.get("high_conviction", 0), t.get("score_at_entry", 0),
                    ])
                    _mtm_written += 1
            _manifest_rows.append(("daily_open_trades_mtm.csv", _mtm_written))
            log.info("[R1 Evidence] daily_open_trades_mtm.csv appended (%d open trades)", _mtm_written)

            # ── Artifact 4: daily_hc_funnel_snapshot.csv ──
            _funnel_path = _audit_dir / "daily_hc_funnel_snapshot.csv"
            _funnel_header = not _funnel_path.exists()
            from config import (HC_MIN_SCORE, HC_RSI_RANGE, HC_DELIVERY_MIN,
                                HC_ATR_RANGE, HC_RISK_MAX, HC_MIN_RISK_REWARD,
                                HC_REQUIRE_MACD_BULLISH, HC_REQUIRE_VOLUME,
                                HC_MIN_SIGNALS_BULLISH)
            # Sequential funnel attrition
            _universe = len(results)
            _pool = results[:]
            _pool = [r for r in _pool if HC_RSI_RANGE[0] <= (r.get("rsi") or 0) <= HC_RSI_RANGE[1]]
            _after_rsi = len(_pool)
            _pool = [r for r in _pool if (r.get("delivery_pct") or 50.0) >= HC_DELIVERY_MIN]
            _after_dlv = len(_pool)
            _pool = [r for r in _pool if HC_ATR_RANGE[0] <= (r.get("atr_pct") or 0) <= HC_ATR_RANGE[1]]
            _after_atr = len(_pool)
            _pool = [r for r in _pool if (r.get("risk_score") or 0) <= HC_RISK_MAX]
            _after_risk = len(_pool)
            _pool = [r for r in _pool if (r.get("risk_reward") or 0) >= HC_MIN_RISK_REWARD]
            _after_rr = len(_pool)
            if HC_REQUIRE_MACD_BULLISH:
                _pool = [r for r in _pool if r.get("macd_signal") == "Bullish"]
            _after_vol = len([r for r in _pool if (r.get("volume_ratio") or 1.0) >= HC_REQUIRE_VOLUME])
            _pool = [r for r in _pool if (r.get("volume_ratio") or 1.0) >= HC_REQUIRE_VOLUME]
            _after_score = len([r for r in _pool if (r.get("score") or 0) >= HC_MIN_SCORE])
            with open(_funnel_path, "a", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                if _funnel_header:
                    w.writerow([
                        "Date", "Scan ID", "Release Version", "Observation Day",
                        "HC Threshold Used", "Universe", "After RSI", "After Delivery",
                        "After ATR", "After Risk", "After RR",
                        "After Volume", "After Score", "Final HC",
                    ])
                w.writerow([
                    _today_str, scan_id, _release, _obs_day,
                    HC_MIN_SCORE, _universe, _after_rsi, _after_dlv,
                    _after_atr, _after_risk, _after_rr,
                    _after_vol, _after_score, _hc_count,
                ])
            _manifest_rows.append(("daily_hc_funnel_snapshot.csv", 1))
            log.info("[R1 Evidence] daily_hc_funnel_snapshot.csv appended (Day %d)", _obs_day)

            # ── Manifest: Artifact Health Check ──
            _manifest_path = _audit_dir / "manifest.csv"
            _manifest_hdr = not _manifest_path.exists()
            with open(_manifest_path, "a", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                if _manifest_hdr:
                    w.writerow(["Date", "Scan ID", "Artifact Name", "Rows Written"])
                for _art_name, _art_rows in _manifest_rows:
                    w.writerow([_today_str, scan_id, _art_name, _art_rows])
            log.info("[R1 Evidence] manifest.csv updated (%d artifacts validated)", len(_manifest_rows))

        except Exception as _ev_exc:
            log.warning("[R1 Evidence] Evidence collection failed (non-fatal): %s", _ev_exc)
        # ── END R1 EVIDENCE COLLECTION ───────────────────────────────


        # Subscribe all to live feed
        live_feed.subscribe([r["symbol"] for r in results])

        # Clean up stale detail cache files (72h retention)
        try:
            from cache_layer import cleanup_detail_cache
            cleanup_detail_cache()
        except Exception:
            pass

        # Phase 0A: Mark completed via atomic transition
        transition_scan_state(
            scan_id=scan_id, from_status="running", to_status="completed",
            reason="scan_completed", actor=ACTOR_SYSTEM,
            correlation_id=correlation_id,
        )
        _reached_terminal = True

    except Exception as exc:
        log.error("[%s] Scan failed: %s", correlation_id[:12], exc)
        transition_scan_state(
            scan_id=scan_id, from_status="running", to_status="failed",
            reason=str(exc)[:500], actor=ACTOR_SYSTEM,
            correlation_id=correlation_id,
            error_message=str(exc)[:500],
        )
        _reached_terminal = True
        return
    finally:
        # Phase 0B: GUARANTEED terminal state — if we haven't reached one yet,
        # force it now. This catches edge cases where exceptions bypass the
        # normal completion path.
        if not _reached_terminal:
            log.warning("[%s] Finally block: scan did not reach terminal state — forcing FAILED", correlation_id[:12])
            transition_scan_state(
                scan_id=scan_id, from_status="running", to_status="failed",
                reason="finally_block_recovery", actor=ACTOR_SYSTEM,
                correlation_id=correlation_id,
                error_message="scan_exited_without_terminal_state",
            )
        db.clear_meta_cache()  # Phase 1: ensure fresh metadata after scan
