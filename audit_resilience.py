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

conn = psycopg2.connect(url)
cur = conn.cursor()

# Get all table names
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables = [r[0] for r in cur.fetchall()]

scans = {
    "successful_primary": "scan_auto_1781508493_5568c1", # Before Angel failure
    "successful_fallback": "scan_manual_1781856444_055982" # After Angel failure
}

results = {}

for name, scan_id in scans.items():
    res = {}
    
    # 1. Check scan_runs
    cur.execute("SELECT status, phase, duration_seconds FROM scan_runs WHERE scan_id = %s", (scan_id,))
    scan_run = cur.fetchone()
    if scan_run:
        res["status"] = scan_run[0]
        res["phase"] = scan_run[1]
        res["duration_seconds"] = scan_run[2]
    
    # 2. Check scan_results
    if "scan_results" in tables:
        cur.execute("SELECT COUNT(*) FROM scan_results WHERE scan_id = %s", (scan_id,))
        res["scan_results_count"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM scan_results WHERE scan_id = %s AND (data->>'high_conviction')::boolean = true", (scan_id,))
        res["high_conviction_count"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM scan_results WHERE scan_id = %s AND data->>'score' IS NOT NULL AND (data->>'score')::int > 0", (scan_id,))
        res["scored_count"] = cur.fetchone()[0]
        
    # 3. final_scores (if it exists)
    if "final_scores" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM final_scores WHERE scan_id = %s", (scan_id,))
            res["final_scores_count"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["final_scores_count"] = str(e)
            
    # 4. recommendation_snapshots (or similar)
    if "recommendation_snapshots" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM recommendation_snapshots WHERE scan_id = %s", (scan_id,))
            res["recommendation_snapshots"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["recommendation_snapshots"] = str(e)
            
    # 5. research_snapshots_v2
    if "research_snapshots_v2" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM research_snapshots_v2 WHERE scan_id = %s", (scan_id,))
            res["research_snapshots"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["research_snapshots"] = str(e)
            
    # 6. paper_orders
    if "paper_orders" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM paper_orders WHERE scan_id = %s", (scan_id,))
            res["paper_orders"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["paper_orders"] = str(e)
            
    results[name] = res

print(json.dumps(results, indent=2))
conn.close()
