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

try:
    conn = psycopg2.connect(url, connect_timeout=10)
    cur = conn.cursor()
    
    # Find scans that logged SCAN_COMPLETED but are marked as 'failed' in scan_runs
    query = """
    SELECT r.scan_id, r.status as run_status, e.created_at as completed_event_time
    FROM scan_runs r
    JOIN scan_event_audit e ON r.scan_id = e.scan_id
    WHERE e.event_type = 'SCAN_COMPLETED'
      AND r.status = 'failed'
    """
    cur.execute(query)
    split_brain_victims = cur.fetchall()
    
    print("Victims (Completed but marked Failed):", split_brain_victims)
    
    # What about watchdog recoveries?
    cur.execute("SELECT scan_id, start_time, end_time, error_message FROM scan_runs WHERE status = 'failed' AND error_message LIKE '%Heartbeat timeout%'")
    zombies = cur.fetchall()
    
    print(f"Total Watchdog zombies in PG: {len(zombies)}")
    
    conn.close()
except Exception as e:
    print("PG Error:", e)
