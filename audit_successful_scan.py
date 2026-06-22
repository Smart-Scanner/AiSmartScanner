import os, psycopg2
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url)
cur = conn.cursor()

scan_id = 'scan_manual_1781856444_055982'

cur.execute("SELECT status, phase, start_time, end_time FROM scan_runs WHERE scan_id=%s", (scan_id,))
row = cur.fetchone()
print("SCAN_RUNS RECORD:", row)

cur.execute("SELECT event_type, details, created_at FROM scan_event_audit WHERE scan_id=%s AND event_type NOT LIKE 'SYMBOL%%' ORDER BY created_at ASC", (scan_id,))
for r in cur.fetchall():
    print(f"[{r[2]}] {r[0]}: {str(r[1])[:80]}")

cur.execute("SELECT COUNT(*) FROM scan_results WHERE scan_id=%s", (scan_id,))
print("RESULTS COUNT:", cur.fetchone()[0])

conn.close()
