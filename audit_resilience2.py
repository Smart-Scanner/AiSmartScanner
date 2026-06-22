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

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables = [r[0] for r in cur.fetchall()]

scans = {
    "before_failure": "scan_auto_1781508493_5568c1",
    "after_failure_fallback": "scan_manual_1781856444_055982"
}

results = {}

for name, scan_id in scans.items():
    res = {}
    
    cur.execute("SELECT status, phase, duration_seconds, processed_count, failed_count FROM scan_runs WHERE scan_id = %s", (scan_id,))
    scan_run = cur.fetchone()
    if scan_run:
        res["status"] = scan_run[0]
        res["phase"] = scan_run[1]
        res["duration_seconds"] = scan_run[2]
        res["processed_count"] = scan_run[3]
        res["failed_count"] = scan_run[4]
    
    # 2. Check final_scores 
    if "final_scores" in tables:
        try:
            # Let's check columns first
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='final_scores'")
            cols = [r[0] for r in cur.fetchall()]
            if "scan_id" in cols:
                cur.execute("SELECT COUNT(*) FROM final_scores WHERE scan_id = %s", (scan_id,))
                res["final_scores_count"] = cur.fetchone()[0]
            else:
                res["final_scores_count"] = "no scan_id column"
        except Exception as e:
            conn.rollback()
            res["final_scores_count"] = str(e)
            
    # 3. recommendation_snapshots 
    if "recommendation_snapshots" in tables:
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='recommendation_snapshots'")
            cols = [r[0] for r in cur.fetchall()]
            if "scan_id" in cols:
                cur.execute("SELECT COUNT(*) FROM recommendation_snapshots WHERE scan_id = %s", (scan_id,))
                res["recommendation_snapshots"] = cur.fetchone()[0]
            else:
                res["recommendation_snapshots"] = "no scan_id column"
        except Exception as e:
            conn.rollback()
            res["recommendation_snapshots"] = str(e)
            
    # 4. research_snapshots_v2
    if "research_snapshots_v2" in tables:
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='research_snapshots_v2'")
            cols = [r[0] for r in cur.fetchall()]
            if "scan_id" in cols:
                cur.execute("SELECT COUNT(*) FROM research_snapshots_v2 WHERE scan_id = %s", (scan_id,))
                res["research_snapshots"] = cur.fetchone()[0]
            else:
                res["research_snapshots"] = "no scan_id column"
        except Exception as e:
            conn.rollback()
            res["research_snapshots"] = str(e)
            
    # 5. paper_orders
    if "paper_orders" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM paper_orders WHERE scan_id = %s", (scan_id,))
            res["paper_orders"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["paper_orders"] = str(e)
            
    # 6. score_audit
    if "score_audit" in tables:
        try:
            cur.execute("SELECT COUNT(*) FROM score_audit WHERE scan_id = %s", (scan_id,))
            res["score_audit"] = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM score_audit WHERE scan_id = %s AND high_conviction = true", (scan_id,))
            res["hc_count"] = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            res["score_audit"] = str(e)
            
    results[name] = res

print(json.dumps(results, indent=2))
conn.close()
