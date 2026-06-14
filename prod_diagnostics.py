import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()
db_url = os.getenv("DATABASE_URL")

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor(cursor_factory=RealDictCursor)

def run_q(query, params=None):
    try:
        cur.execute(query, params)
        if cur.description:
            return cur.fetchall()
        return []
    except Exception as e:
        return f"ERROR: {e}"

print("=== PHASE 6 & 7 PROD STATE FORENSICS ===")

# 1. scan_runs
print("\n[scan_runs] Active / Recent Scans:")
for row in run_q("SELECT scan_id, status, mode, processed_count, failed_count, candidate_count, start_time, end_time FROM scan_runs ORDER BY start_time DESC LIMIT 5"):
    print(dict(row))

# 2. current_scan_state
print("\n[current_scan_state]:")
for row in run_q("SELECT * FROM current_scan_state"):
    print(dict(row))

# 3. universe_chunk_runs
print("\n[universe_chunk_runs] Running or Recent chunks:")
for row in run_q("SELECT chunk_name, status, symbols_processed, started_at, completed_at, scan_id FROM universe_chunk_runs ORDER BY started_at DESC LIMIT 10"):
    print(dict(row))

# 4. scan_event_audit (Count and recent)
print("\n[scan_event_audit] Recent errors or watchdog events:")
for row in run_q("SELECT event_type, details, created_at FROM scan_event_audit WHERE event_type IN ('WATCHDOG_RECOVERED', 'SCAN_FAILED', 'CHUNK_FAILED', 'SYMBOL_FAILED') ORDER BY created_at DESC LIMIT 10"):
    print(dict(row))

# 5. SQLite fallback events evidence (from logs or db? we can't query SQLite from here easily, but we can query Postgres schema details)
print("\n[Schema Drift] Checking hash_chain in scan_state_transitions:")
print(run_q("SELECT column_name FROM information_schema.columns WHERE table_name='scan_state_transitions' AND column_name='hash_chain'"))

conn.close()
