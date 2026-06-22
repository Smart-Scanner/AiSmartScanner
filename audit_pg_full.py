import os
import psycopg2
from dotenv import load_dotenv
import json

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url, connect_timeout=10)
cur = conn.cursor()

incidents = []

# 1. Look for rowcount=0 logs in scan_event_audit (maybe they log it?)
cur.execute("SELECT scan_id, created_at, details FROM scan_event_audit WHERE details LIKE '%rowcount=0%'")
for r in cur.fetchall():
    incidents.append({"scan_id": r[0], "timestamp": str(r[1]), "evidence": "rowcount=0 transition failure in audit", "confidence": "HIGH"})

# 2. Look for scans where there is NO transition to the final state in scan_state_transitions, 
#    but the scan is failed or completed. Wait, Watchdog inserts a transition when it kills zombies.
cur.execute("""
    SELECT r.scan_id, r.status, r.error_message
    FROM scan_runs r
    LEFT JOIN scan_state_transitions t ON r.scan_id = t.scan_id AND t.new_state = r.status
    WHERE r.status IN ('failed', 'completed') AND t.id IS NULL
""")
for r in cur.fetchall():
    # If Watchdog didn't catch it and there's no transition, it's weird
    incidents.append({"scan_id": r[0], "timestamp": "UNKNOWN", "evidence": f"Scan is {r[1]} but missing transition record", "confidence": "MEDIUM"})

# 3. Look for status mismatches between current_scan_state and scan_runs
cur.execute("""
    SELECT c.scan_id, c.status as c_status, r.status as r_status
    FROM current_scan_state c
    JOIN scan_runs r ON c.scan_id = r.scan_id
    WHERE c.status != r.status
""")
for r in cur.fetchall():
    incidents.append({"scan_id": r[0], "timestamp": "CURRENT", "evidence": f"current_scan_state is {r[1]} but scan_runs is {r[2]}", "confidence": "HIGH"})

# 4. Resume state pointing to completed/failed scans
cur.execute("""
    SELECT s.scan_id, s.status as s_status, r.status as r_status, s.last_heartbeat
    FROM scan_resume_state s
    JOIN scan_runs r ON s.scan_id = r.scan_id
    WHERE r.status IN ('failed', 'completed')
""")
for r in cur.fetchall():
    incidents.append({"scan_id": r[0], "timestamp": str(r[3]), "evidence": f"Resume state exists (status={r[1]}) but scan_runs is {r[2]}", "confidence": "HIGH"})

print(json.dumps(incidents, indent=2))
conn.close()
