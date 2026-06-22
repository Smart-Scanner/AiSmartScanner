import db
import time
import threading
from scanner import run_full_scan, ScanContext

def main():
    print("=== PROD VALIDATION 1: Legacy State ===")
    rows = db.execute_db("SELECT scan_id, universe_version, candidate_count, processed_count, status FROM scan_runs ORDER BY start_time DESC LIMIT 5", fetch="all")
    for r in rows:
        print(f"Scan {r['scan_id'][:15]}: version={r.get('universe_version')}, candidates={r.get('candidate_count')}, processed={r.get('processed_count')}, status={r['status']}")

    print("\n=== PROD VALIDATION 2: Active Version Mutation Test ===")
    
    # Ensure active universe version is set
    original_active = db.get_meta("active_universe_version") or "UNIVERSE_v014"
    db.set_meta("active_universe_version", original_active)
    
    # We will trigger a scan in a background thread
    print(f"Starting scan with active version: {original_active}")
    
    def background_scan():
        ctx = ScanContext.create(trigger_source="validation_test", user_id="system", mode="manual")
        run_full_scan(ctx)
        
    t = threading.Thread(target=background_scan)
    t.start()
    
    # Wait a few seconds for scan to start and lock the version
    time.sleep(10)
    
    # Mutate the active version
    mutated_version = "UNIVERSE_v999"
    print(f"Mutating active version in meta to: {mutated_version}")
    db.set_meta("active_universe_version", mutated_version)
    
    # Wait for the scan to finish (or we can just check the DB now, but it's better to let it finish to check counts)
    print("Waiting for scan to complete... (This may take a few minutes)")
    t.join()
    
    # Restore original version
    db.set_meta("active_universe_version", original_active)
    
    print("\nChecking the latest scan run...")
    latest_scan = db.execute_db("SELECT * FROM scan_runs WHERE status = 'COMPLETED' ORDER BY end_time DESC LIMIT 1", fetch="one")
    if latest_scan:
        print(f"Latest Scan ID: {latest_scan['scan_id']}")
        print(f"Universe Version Used: {latest_scan.get('universe_version')}")
        if latest_scan.get("universe_version") == original_active:
            print("✅ PASS: Scan locked the version and ignored mutation.")
        else:
            print("❌ FAIL: Scan used the mutated version.")
    else:
        print("❌ FAIL: No completed scan found.")

    print("\n=== PROD VALIDATION 3: Scan Result Isolation ===")
    grouped = db.execute_db("SELECT scan_id, COUNT(*) as c FROM scan_results_v2 GROUP BY scan_id ORDER BY COUNT(*) DESC LIMIT 5", fetch="all")
    for g in grouped:
        print(f"Scan ID {g['scan_id'][:15]} has {g['c']} results.")

    print("\n=== PROD VALIDATION 4: UI Consistency ===")
    ui_count_row = db.execute_db("SELECT COUNT(*) as c FROM scan_results_v2 WHERE scan_id = ?", (latest_scan["scan_id"] if latest_scan else "scan_legacy_migration",), fetch="one")
    ui_count = ui_count_row["c"] if ui_count_row else 0
    print(f"DB Expected UI Count (for latest scan): {ui_count}")
    
    from db import get_result_count
    actual_ui_count = get_result_count()
    print(f"Actual API/UI Count returned by get_result_count(): {actual_ui_count}")
    if ui_count == actual_ui_count:
        print("✅ PASS: UI matches isolated DB count.")
    else:
        print("❌ FAIL: UI count mismatch.")

if __name__ == "__main__":
    main()
