import sqlite3
import json

db_path = "cache/screener.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

scans = {
    "before_failure": "scan_auto_1781508493_5568c1",
    "after_failure": "scan_manual_1781856444_055982"
}

results = {}

for name, scan_id in scans.items():
    res = {}
    
    # 1. Check scan_runs
    cur.execute("SELECT status, phase, duration_seconds, processed_count, failed_count FROM scan_runs WHERE scan_id = ?", (scan_id,))
    scan_run = cur.fetchone()
    if scan_run:
        res["status"] = scan_run['status']
        res["phase"] = scan_run['phase']
        res["execution_time_s"] = scan_run['duration_seconds']
        res["processed_count"] = scan_run['processed_count']
        res["failed_count"] = scan_run['failed_count']
    else:
        res["status"] = "NOT_FOUND"
        
    # 2. Check final_scores (since scan_results doesn't have scan_id, final_scores does)
    try:
        cur.execute("SELECT COUNT(*) as count FROM final_scores WHERE scan_id = ?", (scan_id,))
        res["final_scores_count"] = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM final_scores WHERE scan_id = ? AND high_conviction = 1", (scan_id,))
        res["hc_count"] = cur.fetchone()['count']
    except Exception as e:
        res["final_scores_count"] = str(e)
        
    # 3. Check recommendation_snapshots
    try:
        cur.execute("SELECT COUNT(*) as count FROM recommendation_snapshots WHERE scan_id = ?", (scan_id,))
        res["recommendation_snapshots"] = cur.fetchone()['count']
    except Exception as e:
        res["recommendation_snapshots"] = str(e)
        
    # 4. Check research_snapshots_v2
    try:
        cur.execute("SELECT COUNT(*) as count FROM research_snapshots_v2 WHERE scan_id = ?", (scan_id,))
        res["research_snapshots"] = cur.fetchone()['count']
    except Exception as e:
        res["research_snapshots"] = str(e)
        
    # 5. Check paper_orders (Portfolio Lab)
    try:
        cur.execute("SELECT COUNT(*) as count FROM paper_orders WHERE scan_id = ?", (scan_id,))
        res["paper_orders"] = cur.fetchone()['count']
    except Exception as e:
        res["paper_orders"] = str(e)
        
    # 6. Check scan_event_audit for SYMBOL_COMPLETED (Angel success) vs Phase 2 fallback scores
    try:
        cur.execute("SELECT COUNT(*) as count FROM scan_event_audit WHERE scan_id = ? AND event_type = 'SYMBOL_COMPLETED'", (scan_id,))
        res["angel_success_count"] = cur.fetchone()['count']
    except Exception as e:
        res["angel_success_count"] = str(e)

    results[name] = res

print(json.dumps(results, indent=2))
conn.close()
