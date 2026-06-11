import os
from dotenv import load_dotenv

load_dotenv('.env')
import db

db._get_pg_pool()
conn = db._pg_pool.getconn()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'scan_runs'")
        cols = cur.fetchall()
        print("scan_runs columns:", [c[0] for c in cols])
        
        cur.execute("""
            SELECT scan_id, mode, processed_count, candidate_count, config_snapshot 
            FROM scan_runs 
            ORDER BY start_time DESC LIMIT 3
        """)
        rows = cur.fetchall()
        print("scan_runs records:")
        for r in rows:
            print(r)
except Exception as e:
    print("Error:", e)
finally:
    db._pg_pool.putconn(conn)
