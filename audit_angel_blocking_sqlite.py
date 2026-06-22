import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
import os

db_path = os.path.join("cache", "screener.db")
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found.")
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

print("Gathering evidence for the last 7 days...")

# SQLite doesn't have date_trunc, we use strftime
cur.execute("""
    SELECT 
        strftime('%Y-%m-%d %H:00:00', created_at) as hr,
        event_type,
        COUNT(*) 
    FROM scan_event_audit
    WHERE created_at >= datetime('now', '-7 days')
      AND event_type IN ('SYMBOL_COMPLETED', 'SYMBOL_FAILED')
    GROUP BY hr, event_type
    ORDER BY hr ASC
""")
hourly_stats = cur.fetchall()

cur.execute("""
    SELECT 
        scan_id,
        MIN(created_at) as scan_time,
        SUM(CASE WHEN event_type = 'SYMBOL_COMPLETED' THEN 1 ELSE 0 END) as success_count,
        SUM(CASE WHEN event_type = 'SYMBOL_FAILED' THEN 1 ELSE 0 END) as fail_count
    FROM scan_event_audit
    WHERE created_at >= datetime('now', '-7 days')
      AND event_type IN ('SYMBOL_COMPLETED', 'SYMBOL_FAILED')
    GROUP BY scan_id
    ORDER BY scan_time ASC
""")
scan_stats = cur.fetchall()

cur.execute("""
    SELECT scan_id, created_at, details
    FROM scan_event_audit
    WHERE event_type = 'SYMBOL_COMPLETED'
    ORDER BY created_at DESC
    LIMIT 1
""")
last_success = cur.fetchone()

# First fail after the last success
if last_success:
    last_success_time = last_success[1]
else:
    last_success_time = '2000-01-01'

cur.execute("""
    SELECT scan_id, created_at, details
    FROM scan_event_audit
    WHERE event_type = 'SYMBOL_FAILED' AND details LIKE '%Empty df%'
      AND created_at > ?
    ORDER BY created_at ASC
    LIMIT 1
""", (last_success_time,))
first_fail = cur.fetchone()

print("\n=== SUMMARY ===")
print(f"Last Successful Call: {last_success[1] if last_success else 'None'} (Scan: {last_success[0] if last_success else 'N/A'})")
print(f"First Failure After Success: {first_fail[1] if first_fail else 'None'} (Scan: {first_fail[0] if first_fail else 'N/A'})")

print("\n=== HOURLY STATS (Last 7 Days) ===")
stats_by_hour = defaultdict(lambda: {'success': 0, 'fail': 0})
for hr, event_type, count in hourly_stats:
    if event_type == 'SYMBOL_COMPLETED':
        stats_by_hour[hr]['success'] = count
    else:
        stats_by_hour[hr]['fail'] = count

for hr in sorted(stats_by_hour.keys()):
    print(f"{hr} -> SUCCESS: {stats_by_hour[hr]['success']}, FAILED: {stats_by_hour[hr]['fail']}")

print("\n=== SCAN STATS (Last 7 Days) ===")
for scan_id, scan_time, sc, fc in scan_stats:
    print(f"{scan_time} | {scan_id} | SUCCESS: {sc} | FAILED: {fc} | TOTAL: {sc+fc}")

conn.close()
