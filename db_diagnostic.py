"""Quick diagnostic: check what's actually in the Railway DB"""
import os, psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv()

URL = os.getenv('DATABASE_URL')
conn = psycopg2.connect(URL, cursor_factory=RealDictCursor, connect_timeout=10)
cur = conn.cursor()
print("=== CONNECTED TO RAILWAY DB ===\n")

# 1. Check scan_runs
print("--- scan_runs (last 5) ---")
cur.execute("SELECT scan_id, status, start_time, end_time, duration_seconds FROM scan_runs ORDER BY start_time DESC LIMIT 5")
for r in cur.fetchall():
    print(f"  {r['scan_id'][:30]} | status={r['status']} | start={r['start_time']} | end={r['end_time']} | dur={r['duration_seconds']}s")

# 2. Check scan_results_v2 count
print("\n--- scan_results_v2 counts ---")
cur.execute("SELECT scan_id, COUNT(*) as cnt, COUNT(slim_data) as slim_cnt FROM scan_results_v2 GROUP BY scan_id ORDER BY cnt DESC LIMIT 5")
for r in cur.fetchall():
    print(f"  scan_id={r['scan_id'][:30]} | rows={r['cnt']} | slim={r['slim_cnt']}")

# 3. Check scan_results (old table)
print("\n--- scan_results (OLD table) ---")
try:
    cur.execute("SELECT COUNT(*) as cnt FROM scan_results")
    r = cur.fetchone()
    print(f"  rows={r['cnt']}")
except Exception as e:
    print(f"  Table doesn't exist or error: {e}")
    conn.rollback()

# 4. Check get_latest_completed_scan_id equivalent
print("\n--- Latest completed scan (LOWER match) ---")
cur.execute("SELECT scan_id, status FROM scan_runs WHERE LOWER(status) = 'completed' ORDER BY end_time DESC LIMIT 1")
r = cur.fetchone()
if r:
    print(f"  scan_id={r['scan_id']} | status={r['status']}")
else:
    print("  NO COMPLETED SCANS FOUND!")

# 5. Check current_scan_state
print("\n--- current_scan_state ---")
try:
    cur.execute("SELECT * FROM current_scan_state LIMIT 1")
    r = cur.fetchone()
    if r:
        for k, v in r.items():
            print(f"  {k}={v}")
    else:
        print("  EMPTY")
except Exception as e:
    print(f"  Error: {e}")
    conn.rollback()

# 6. Check scan_meta for key values
print("\n--- key scan_meta values ---")
for key in ['last_scan', 'scan_id', 'heatmap', 'auto_scan_enabled']:
    cur.execute("SELECT value FROM scan_meta WHERE key=%s", (key,))
    r = cur.fetchone()
    val = r['value'][:80] if r and r['value'] else 'NULL'
    print(f"  {key} = {val}")

conn.close()
print("\n=== DONE ===")
