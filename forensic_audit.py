import sys
import os
import json
import sqlite3

# Import app modules
try:
    import db
except ImportError:
    pass

evidence = {}

# 1. SQLite Evidence
try:
    conn = sqlite3.connect('cache/screener.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM universe_catalog")
    evidence['sqlite_total_rows'] = cur.fetchone()['c']
    
    cur.execute("SELECT COUNT(*) as c FROM universe_catalog WHERE is_active = 1")
    evidence['sqlite_active_rows'] = cur.fetchone()['c']
    
    cur.execute("SELECT * FROM universe_catalog LIMIT 5")
    evidence['sqlite_sample'] = [dict(r) for r in cur.fetchall()]
    conn.close()
except Exception as e:
    evidence['sqlite_error'] = str(e)

# 2. Postgres Evidence
try:
    pg_conn = db.get_pg_conn()
    if pg_conn:
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM universe_catalog")
            evidence['pg_total_rows'] = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM universe_catalog WHERE is_active = TRUE")
            evidence['pg_active_rows'] = cur.fetchone()[0]
            
            cur.execute("SELECT symbol, company_name, market_cap_bucket FROM universe_catalog LIMIT 5")
            evidence['pg_sample'] = cur.fetchall()
        pg_conn.close()
    else:
        evidence['pg_error'] = "No PG connection returned"
except Exception as e:
    evidence['pg_error'] = str(e)

print(json.dumps(evidence, indent=2))
