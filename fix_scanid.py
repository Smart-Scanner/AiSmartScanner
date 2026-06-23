import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(os.getenv('DATABASE_URL'), connect_timeout=10)
cur = conn.cursor()

# Get latest completed scan_id
cur.execute("SELECT scan_id FROM scan_runs WHERE LOWER(status) = 'completed' ORDER BY end_time DESC LIMIT 1")
row = cur.fetchone()
if row:
    latest = row[0]
    print(f"Latest completed scan: {latest}")
    cur.execute("UPDATE scan_results_v2 SET scan_id = %s WHERE scan_id = 'legacy_fallback'", (latest,))
    print(f"Updated {cur.rowcount} rows from legacy_fallback -> {latest}")
    conn.commit()
else:
    print("No completed scan found!")
conn.close()
print("DONE")
