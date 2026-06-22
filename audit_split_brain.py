import sqlite3
import json
from datetime import datetime

c = sqlite3.connect('cache/screener.db')
c.row_factory = sqlite3.Row

incidents = []
suspected = []

try:
    # 1. Check scan_runs for scans marked running but heartbeat stopped > 15 mins ago
    runs = c.execute("SELECT scan_id, status, phase, last_heartbeat FROM scan_runs WHERE status = 'running'").fetchall()
    for r in runs:
        if r['last_heartbeat']:
            hb_time = datetime.fromisoformat(r['last_heartbeat'])
            if (datetime.now() - hb_time).total_seconds() > 900:
                incidents.append({
                    "scan_id": r['scan_id'],
                    "timestamp": r['last_heartbeat'],
                    "evidence": f"Scan is 'running' but heartbeat stopped at {r['last_heartbeat']} (>15m ago)",
                    "confidence": "HIGH (Zombie)"
                })

    # 2. Status mismatch between current_scan_state and scan_runs
    current_state = c.execute("SELECT scan_id, status FROM current_scan_state WHERE id=1").fetchone()
    if current_state and current_state['scan_id']:
        sid = current_state['scan_id']
        run_status = c.execute("SELECT status FROM scan_runs WHERE scan_id = ?", (sid,)).fetchone()
        if run_status and run_status['status'] != current_state['status']:
            suspected.append({
                "scan_id": sid,
                "timestamp": str(datetime.now()),
                "evidence": f"current_scan_state status ({current_state['status']}) != scan_runs status ({run_status['status']})",
                "confidence": "MEDIUM (Mismatch)"
            })

    # 3. Resume state pointing to completed/failed scans
    resumes = c.execute("SELECT scan_id, status FROM scan_resume_state").fetchall()
    for rs in resumes:
        run_status = c.execute("SELECT status FROM scan_runs WHERE scan_id = ?", (rs['scan_id'],)).fetchone()
        if run_status and run_status['status'] in ['completed', 'failed']:
            incidents.append({
                "scan_id": rs['scan_id'],
                "timestamp": str(datetime.now()),
                "evidence": f"Resume state exists for {run_status['status']} scan",
                "confidence": "HIGH (Orphaned Resume)"
            })

    # 4. Watchdog recovery in scan_event_audit
    events = c.execute("SELECT scan_id, created_at, details FROM scan_event_audit WHERE event_type LIKE '%ZOMBIE%' OR details LIKE '%Zombie%' OR details LIKE '%WATCHDOG%'").fetchall()
    for e in events:
        incidents.append({
            "scan_id": e['scan_id'],
            "timestamp": e['created_at'],
            "evidence": f"Watchdog recovery event: {e['details']}",
            "confidence": "HIGH (Watchdog Intervention)"
        })

    # 5. Check scan_lock owner persisted after scan termination
    locks = c.execute("SELECT scan_id, owner_id FROM scan_lock WHERE scan_id IS NOT NULL").fetchall()
    for lk in locks:
        run_status = c.execute("SELECT status FROM scan_runs WHERE scan_id = ?", (lk['scan_id'],)).fetchone()
        if run_status and run_status['status'] in ['completed', 'failed']:
            incidents.append({
                "scan_id": lk['scan_id'],
                "timestamp": str(datetime.now()),
                "evidence": f"Lock held by {lk['owner_id']} for {run_status['status']} scan",
                "confidence": "HIGH (Orphaned Lock)"
            })
            
    # 6. running scan with no matching transition
    trans = c.execute("SELECT scan_id FROM scan_state_transitions WHERE new_state IN ('failed', 'completed')").fetchall()
    trans_set = set([t['scan_id'] for t in trans])
    runs_ended = c.execute("SELECT scan_id, status FROM scan_runs WHERE status IN ('failed', 'completed')").fetchall()
    for r in runs_ended:
        if r['scan_id'] not in trans_set:
            suspected.append({
                "scan_id": r['scan_id'],
                "timestamp": str(datetime.now()),
                "evidence": f"Scan is {r['status']} in scan_runs but no transition logged in SQLite",
                "confidence": "LOW (Could be in PG)"
            })

except Exception as e:
    print("Error querying:", e)

with open('split_brain_audit_results.json', 'w') as f:
    json.dump({"incidents": incidents, "suspected": suspected}, f, indent=2)

print("Saved to split_brain_audit_results.json")
