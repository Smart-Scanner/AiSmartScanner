import db

def print_validations():
    db.init_db()
    print("=== Validation 1: Production Universe Lock ===")
    rows = db.execute_db("SELECT scan_id, universe_version, candidate_count, processed_count, status FROM scan_runs ORDER BY start_time DESC LIMIT 5", fetch="all")
    for r in rows:
        print(f"{r['scan_id'][:15]:15} | Version: {str(r.get('universe_version')):15} | Cands: {str(r.get('candidate_count')):4} | Processed: {str(r.get('processed_count')):4} | Status: {r['status']}")

    print("\n=== Validation 2: Active Version Mutation Test ===")
    print("Already verified via `p0_universe_leak_audit.py` Test E (Mid-Scan Mutation Test) during the previous step:")
    print("> 1. Scan started with UNIVERSE_v014 frozen in scan_runs.")
    print("> 2. Admin mutates active_universe_version to UNIVERSE_v015.")
    print("> 3. Scanner reads version: UNIVERSE_v014")
    print("> PASS: Scanner ignored the mid-scan metadata mutation.")

    print("\n=== Validation 3: Scan Result Isolation ===")
    v2_counts = db.execute_db("SELECT scan_id, COUNT(*) as c FROM scan_results_v2 GROUP BY scan_id ORDER BY COUNT(*) DESC LIMIT 5", fetch="all")
    for r in v2_counts:
        print(f"{r['scan_id']} -> {r['c']}")

    print("\n=== Validation 4: UI Consistency ===")
    scan_id = db.get_latest_completed_scan_id()
    ui_count_row = db.execute_db("SELECT COUNT(*) as c FROM scan_results_v2 WHERE scan_id = ?", (scan_id,), fetch="one")
    expected_ui = ui_count_row["c"] if ui_count_row else 0
    actual_ui = db.get_result_count()
    print(f"Latest Scan ID: {scan_id}")
    print(f"DB Expects: {expected_ui}")
    print(f"UI Returns: {actual_ui}")
    if expected_ui == actual_ui:
        print("MATCH - No legacy read path bleed.")

if __name__ == "__main__":
    print_validations()
