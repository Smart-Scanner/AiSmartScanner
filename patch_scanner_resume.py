import re

def patch_scanner_resume():
    file_path = "scanner.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update signature
    content = content.replace("def run_full_scan(context: ScanContext = None):", "def run_full_scan(context: ScanContext = None, resume_from_scan_id: str = None):")

    # 2. Inside run_full_scan, before global_i = 0
    # Search for chunks = universe.get_universe_chunks(all_symbols)
    
    inject_str = """
        chunks = universe.get_universe_chunks(all_symbols)
        
        # --- PHASE F: INTRA-CHUNK RESUME LOGIC ---
        resume_states = {}
        if resume_from_scan_id:
            try:
                resume_states = db.get_chunk_run_states(resume_from_scan_id)
                log.info("[%s] Resuming from %s, found %d chunk states.", correlation_id[:12], resume_from_scan_id, len(resume_states))
            except Exception as e:
                log.warning("[%s] Failed to fetch resume states: %s", correlation_id[:12], e)

        filtered_chunks = []
        for c_name, c_symbols in chunks:
            if c_name in resume_states:
                status, processed = resume_states[c_name]
                if status == "COMPLETED":
                    log.info("[%s] Skipping completed chunk: %s", correlation_id[:12], c_name)
                    continue
                elif processed > 0:
                    log.info("[%s] Resuming chunk: %s from offset %d", correlation_id[:12], c_name, processed)
                    filtered_chunks.append((c_name, c_symbols[processed:]))
                else:
                    filtered_chunks.append((c_name, c_symbols))
            else:
                filtered_chunks.append((c_name, c_symbols))
        chunks = filtered_chunks
        # ----------------------------------------
"""
    content = content.replace("        chunks = universe.get_universe_chunks(all_symbols)", inject_str)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("scanner.py successfully patched for resume logic.")

if __name__ == "__main__":
    patch_scanner_resume()
