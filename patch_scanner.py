import re
import sys

def patch_scanner():
    file_path = "scanner.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # The region to replace is from "global_i = 0\n        for chunk_name, chunk_symbols in chunks:"
    # all the way to "scan_state.update(phase="phase1_done")"
    
    start_pattern = r'        global_i = 0\n        for chunk_name, chunk_symbols in chunks:'
    end_pattern = r'        scan_state\.update\(phase="phase1_done"\)'
    
    # Let's find the exact indices
    start_idx = content.find("        global_i = 0\n        for chunk_name, chunk_symbols in chunks:")
    end_idx = content.find('        scan_state.update(phase="phase1_done")')
    
    if start_idx == -1 or end_idx == -1:
        print("Could not find start or end index.")
        sys.exit(1)
        
    before = content[:start_idx]
    after = content[end_idx:]
    
    replacement = """
        def _process_chunk_worker(chunk_name, chunk_symbols, scan_id, correlation_id, nifty_1m, regime, total):
            from data_provider import provider_manager
            chunk_results = []
            chunk_failed = 0
            chunk_processed = 0
            failed_syms = []
            scored_syms = set()
            
            provider = provider_manager.acquire_active_provider(role="RESEARCH")
            log.info("[%s] Worker acquired provider: %s for %s", correlation_id[:12], provider.name, chunk_name)
            
            try:
                for sym in chunk_symbols:
                    if db.check_scan_status(scan_id) not in ("running",):
                        break
                    if get_scan_cancel_requested():
                        break

                    sym_start_time = time.monotonic()
                    try:
                        df = provider.fetch_historical(sym, days=DATA_LOOKBACK_DAYS)
                        api_duration = time.monotonic() - sym_start_time
                        
                        anal_start = time.monotonic()
                        if df is not None and not df.empty and len(df) >= 50:
                            r = fetch_and_analyze(sym, nifty_1m, regime, ext_df=df)
                            anal_duration = time.monotonic() - anal_start
                            if r:
                                chunk_results.append(r)
                                scored_syms.add(sym)
                                db.log_scan_event(scan_id, "SYMBOL_COMPLETED", f"Sym: {sym}, Chunk: {chunk_name}, API: {api_duration:.2f}s, Anal: {anal_duration:.2f}s")
                        else:
                            failed_syms.append(sym)
                            chunk_failed += 1
                            db.log_scan_event(scan_id, "SYMBOL_FAILED", f"Sym: {sym}, Chunk: {chunk_name}, Reason: Empty df")
                    except Exception as exc:
                        failed_syms.append(sym)
                        chunk_failed += 1
                        db.log_scan_event(scan_id, "SYMBOL_FAILED", f"Sym: {sym}, Chunk: {chunk_name}, Error: {str(exc)}")
                    
                    chunk_processed += 1
                    time.sleep(1.0) # Gap optimization loop
            finally:
                provider_manager.release_provider(provider.name)
                
            return chunk_name, chunk_symbols, chunk_results, failed_syms, chunk_processed, chunk_failed

        global_i = 0
        _reached_terminal = False
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_chunk = {}
            for chunk_name, chunk_symbols in chunks:
                if not chunk_symbols:
                    continue
                    
                chunk_run_id = db.start_chunk_run(scan_id, chunk_name, len(chunk_symbols))
                db.log_scan_event(scan_id, "CHUNK_STARTED", f"Chunk: {chunk_name} ({len(chunk_symbols)} symbols)")
                log.info("[%s] Queuing chunk: %s (%d symbols)", correlation_id[:12], chunk_name, len(chunk_symbols))
                
                future = executor.submit(_process_chunk_worker, chunk_name, chunk_symbols, scan_id, correlation_id, nifty_1m, regime, total)
                future_to_chunk[future] = (chunk_name, chunk_run_id, len(chunk_symbols))
                
            for future in as_completed(future_to_chunk):
                chunk_name, chunk_run_id, chunk_total = future_to_chunk[future]
                
                current_status = db.check_scan_status(scan_id)
                if current_status not in ("running",):
                    log.error("[%s] SCANNER_ABORT_DETECTED: Status changed to %s", correlation_id[:12], current_status)
                    db.log_scan_event(scan_id, "SCANNER_ABORT_DETECTED", f"Scan status changed to {current_status}")
                    _reached_terminal = True
                    break

                if get_scan_cancel_requested():
                    log.warning("[%s] Scan cancelled by user at %d/%d", correlation_id[:12], global_i, total)
                    db.log_scan_event(scan_id, "SCAN_CANCELLED", "User cancelled scan")
                    transition_scan_state(
                        scan_id=scan_id, from_status="running", to_status="cancelled",
                        reason="user_cancelled", actor=ACTOR_USER,
                        correlation_id=correlation_id,
                    )
                    _reached_terminal = True
                    break
                    
                try:
                    c_name, c_symbols, c_results, c_failed_syms, c_processed, c_failed = future.result()
                    
                    for r in c_results:
                        results.append(r)
                        scored_set.add(r['symbol'])
                    for f in c_failed_syms:
                        failed_symbols.append(f)
                        
                    global_i += c_processed
                    scan_state.set_progress(global_i)
                    if global_i % 50 == 0 or global_i == total:
                        log.info("[%s] Phase 1: %d/%d done, %d scored", correlation_id[:12], global_i, total, len(results))
                        
                    if c_failed > 0 and c_processed == 0:
                        chunk_status = "FAILED"
                    else:
                        chunk_status = "COMPLETED"
                    
                    db.end_chunk_run(chunk_run_id, chunk_status, c_processed, f"{c_failed} failed")
                    db.log_scan_event(scan_id, f"CHUNK_{chunk_status}", f"Chunk: {c_name}, Processed: {c_processed}")
                    
                    if c_results:
                        db.save_results(c_results)
                        
                except Exception as exc:
                    db.end_chunk_run(chunk_run_id, "FAILED", 0, f"Worker exception: {str(exc)}")
                    db.log_scan_event(scan_id, "CHUNK_FAILED", f"Chunk: {chunk_name}, Error: {str(exc)}")
                    
        if _reached_terminal:
            return

"""
    new_content = before + replacement[1:] + after
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("scanner.py successfully patched.")

if __name__ == "__main__":
    patch_scanner()
