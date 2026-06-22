import os
import psycopg2
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url)
cur = conn.cursor()

print("Gathering evidence for the last 7 days...")

# 1. Total Successful vs Failed Calls
cur.execute("""
    SELECT 
        date_trunc('hour', created_at) as hr,
        event_type,
        COUNT(*) 
    FROM scan_event_audit
    WHERE created_at >= NOW() - INTERVAL '7 days'
      AND event_type IN ('SYMBOL_COMPLETED', 'SYMBOL_FAILED')
    GROUP BY hr, event_type
    ORDER BY hr ASC
""")
hourly_stats = cur.fetchall()

# 2. Count by Scan
cur.execute("""
    SELECT 
        scan_id,
        MIN(created_at) as scan_time,
        SUM(CASE WHEN event_type = 'SYMBOL_COMPLETED' THEN 1 ELSE 0 END) as success_count,
        SUM(CASE WHEN event_type = 'SYMBOL_FAILED' THEN 1 ELSE 0 END) as fail_count
    FROM scan_event_audit
    WHERE created_at >= NOW() - INTERVAL '7 days'
      AND event_type IN ('SYMBOL_COMPLETED', 'SYMBOL_FAILED')
    GROUP BY scan_id
    ORDER BY scan_time ASC
""")
scan_stats = cur.fetchall()

# 3. Last successful call and First failed call
cur.execute("""
    SELECT scan_id, created_at, details
    FROM scan_event_audit
    WHERE event_type = 'SYMBOL_COMPLETED'
    ORDER BY created_at DESC
    LIMIT 1
""")
last_success = cur.fetchone()

cur.execute("""
    SELECT scan_id, created_at, details
    FROM scan_event_audit
    WHERE event_type = 'SYMBOL_FAILED' AND details LIKE '%Empty df%'
      AND created_at > (
          SELECT COALESCE(MAX(created_at), '2000-01-01'::timestamp)
          FROM scan_event_audit WHERE event_type = 'SYMBOL_COMPLETED'
      )
    ORDER BY created_at ASC
    LIMIT 1
""")
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
    print(f"{hr.strftime('%Y-%m-%d %H:00')} -> SUCCESS: {stats_by_hour[hr]['success']}, FAILED: {stats_by_hour[hr]['fail']}")

print("\n=== SCAN STATS (Last 7 Days) ===")
for scan_id, scan_time, sc, fc in scan_stats:
    print(f"{scan_time.strftime('%Y-%m-%d %H:%M:%S')} | {scan_id} | SUCCESS: {sc} | FAILED: {fc} | TOTAL: {sc+fc}")

conn.close()
