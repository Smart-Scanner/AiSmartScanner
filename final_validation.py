from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s %(message)s")

import db
import scanner
import sys

# Step 0: Ensure DB initialization
db.init_db()

# Step 1: Force clear the stale scan lock
print("\n=== CLEARING STALE SCAN LOCK ===")
db.execute_db("UPDATE current_scan_state SET status='idle', scan_id=NULL, cancel_requested=0 WHERE id=1")
row = db.execute_db("SELECT scan_id, status FROM current_scan_state WHERE id=1", fetch="one")
print(f"Scan state after clear: {dict(row) if row else 'N/A'}")

# Step 2: Trigger fresh scan
ctx = scanner.ScanContext.create(
    trigger_source="manual",
    user_id="validation_test",
    session_id="validation_session",
    mode="manual",
)
print(f"\n=== STARTING VALIDATION SCAN ===")
print(f"scan_id={ctx.scan_id}")

try:
    scanner.run_full_scan(ctx)
    print("\nScan completed successfully.")
except Exception as e:
    import traceback
    print("\nSCAN FAILED:")
    traceback.print_exc()

# Step 3: Post-scan audit
print("\n=== FINAL VALIDATION AUDIT ===")

# Query 1: Snapshot Health (SQLite compatible)
print("\n[Query 1: Snapshot Health]")
try:
    q1 = """
    SELECT 
        COUNT(*) as total_rows, 
        COUNT(DISTINCT symbol) as unique_symbols, 
        MAX(version) as max_version,
        SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) as active_rows
    FROM research_snapshots_v2
    """
    row1 = db.execute_db(q1, fetch="one")
    print(dict(row1) if row1 else "N/A")
except Exception as e:
    print(f"Q1 Error: {e}")

# Query 2: Organic Growth Test
print("\n[Query 2: Organic Growth]")
try:
    q2 = """
    SELECT symbol, COUNT(*) as versions
    FROM research_snapshots_v2
    GROUP BY symbol
    HAVING COUNT(*) > 1
    """
    rows2 = db.execute_db(q2, fetch="all")
    if rows2:
        for r in rows2:
            print(dict(r))
    else:
        print("0 rows (No version explosion detected)")
except Exception as e:
    print(f"Q2 Error: {e}")

# Query 3: DB Stability Test
print("\n[Query 3: DB Stability]")
try:
    if db.is_postgresql():
        q3 = "SELECT COUNT(*) as c FROM scan_runs WHERE start_time > NOW() - INTERVAL '24 hours' AND status='FAILED'"
    else:
        q3 = "SELECT COUNT(*) as c FROM scan_runs WHERE start_time > datetime('now', '-24 hours') AND status='FAILED'"
    row3 = db.execute_db(q3, fetch="one")
    print(f"Failed scans in last 24h: {row3.get('c') if row3 else 'N/A'}")
except Exception as e:
    print(f"Q3 Error: {e}")
