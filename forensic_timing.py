"""
FORENSIC: Calculate Phase 2 timing for 575 failed symbols
"""
# Production runs on Render (not Railway), so:
BATCH_SIZE = 80
BATCH_DELAY = 10  # seconds between batches
MAX_WORKERS = 4

total_failed = 575
results_from_phase1 = 0  # ALL 575 failed with "Empty df"

# Phase 2 batches
num_batches = (total_failed + BATCH_SIZE - 1) // BATCH_SIZE
print(f"Phase 2 configuration:")
print(f"  failed_symbols: {total_failed}")
print(f"  BATCH_SIZE: {BATCH_SIZE}")
print(f"  BATCH_DELAY: {BATCH_DELAY}s")
print(f"  MAX_WORKERS: {MAX_WORKERS}")
print(f"  Number of batches: {num_batches}")

# Early termination: if first batch scores 0, it breaks
# scanner.py:595: if batch_num >= 1 and jugaad_scored == 0: break
print(f"\n  Batch 1: 80 symbols, submitted via ThreadPoolExecutor(max_workers=4)")
print(f"  If jugaad_scored == 0 after batch 1 → EARLY EXIT (line 596)")
print(f"  If jugaad provides data → continues with 10s delay between batches")

# Time estimates
print(f"\n  SCENARIO A: jugaad_data also blocked (most likely)")
print(f"    Time in Phase 2: ~30-60s (one batch of 80 symbols, all fail)")
print(f"    Then hits data_quality_abort gate (line 601-619)")
print(f"    575/575 = 100% failed → hard abort")
print(f"    Calls transition_scan_state(running -> failed)")
print(f"    Sets _reached_terminal = True")
print(f"    Returns")

print(f"\n  SCENARIO B: jugaad provides data for some symbols")
print(f"    Time: 80 symbols * ~0.5s/symbol = ~40s per batch + 10s delay")
print(f"    {num_batches} batches × 50s = ~{num_batches * 50}s = ~{num_batches * 50 / 60:.1f} min")

# The CRITICAL insight: 
print(f"\n" + "="*70)
print(f"CRITICAL ANALYSIS: The data_quality_abort path")
print(f"="*70)
print(f"  If ALL 575 symbols failed Phase 1 (PROVEN from scan_event_audit)")
print(f"  AND Phase 2 jugaad_data is blocked or also fails:")
print(f"")
print(f"  Line 601: _final_failed = 575 - len(results)")
print(f"  Line 602: results would be EMPTY (0 scored)")
print(f"  Line 603: _final_failed (575) > 5 → TRUE")
print(f"  Line 604: _final_fail_pct = 575/575 = 100%")
print(f"  Line 605: 100% > 5% → TRUE")
print(f"  Line 612-618: transition_scan_state(running -> failed)")
print(f"               _reached_terminal = True")
print(f"               return")
print(f"")
print(f"  BUT WAIT: if results is not empty (chunk_results at line 539)")
print(f"  The chunk finally block at line 538-539 calls save_results(chunk_results)")
print(f"  chunk_results would be EMPTY because all symbols returned Empty df")
print(f"  So save_results would NOT be called (if chunk_results check)")
print(f"")
print(f"  HOWEVER: The SYMBOL_FAILED events show 'Empty df' for ALL symbols")
print(f"  This means df was None or empty at line 497")
print(f"  So fetch_and_analyze was NOT called → r is never set")
print(f"  chunk_results stays empty → save_results(chunk_results) at line 539 is called")
print(f"  but chunk_results is [] → save_results([]) → returns early (line 3096)")
print(f"")
print(f"  This means:")
print(f"  - Phase 1 completed with results = [] and failed_symbols = 575 symbols")
print(f"  - Phase 2 tries jugaad_data for 575 symbols")
print(f"  - If jugaad also fails, data_quality_abort fires")
print(f"  - transition_scan_state('running', 'failed') is called")

print(f"\n" + "="*70)
print(f"TIMELINE RECONSTRUCTION")
print(f"="*70)
print(f"  14:04:10  Scan starts")
print(f"  14:04:39  Phase 1 starts")
print(f"  14:04:43  Chunk started (575 symbols)")
print(f"  14:10:47  Chunk completed — ALL 575 SYMBOL_FAILED (Empty df)")
print(f"            results = [], failed_symbols = [all 575]")
print(f"  14:10:47  save_results([]) → returns immediately")
print(f"  14:10:50  time.sleep(3) → cooling delay")
print(f"  14:10:53  scan_state.update(phase='phase1_done')")
print(f"  14:10:53  Data quality warning logged (100% failed)")
print(f"  14:10:53  Phase 2 starts: jugaad_data for 575 symbols")
print(f"            Batch 1: 80 symbols via ThreadPoolExecutor(4)")
print(f"            Each fetch_and_analyze calls yfinance → external API")
print(f"  ~14:12-14:17  Phase 2 running (jugaad_data batches)")
print(f"  14:18:14  LAST HEARTBEAT")
print(f"  ~14:18:14  Scanner dies (or Phase 2 completes and")
print(f"             data_quality_abort fires at line 612)")
print(f"  14:34:01  Watchdog kills zombie")
print(f"")
print(f"  KEY: Phase 2 runs fetch_and_analyze for EACH of 575 failed symbols")
print(f"  This calls yfinance (external HTTP API)")
print(f"  80 symbols × 4 workers = ~20 concurrent HTTP requests")
print(f"  If yfinance is slow/blocked → Phase 2 can stall for MINUTES")
print(f"  This perfectly explains the 7m27s gap!")
