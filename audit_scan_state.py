import os
import json
import db

def run_audit():
    print("--- SCAN META ---")
    meta_rows = db.execute_db("SELECT key, value FROM scan_meta WHERE key LIKE '%scan%' OR key LIKE '%universe%'", fetch="all")
    for r in meta_rows:
        print(f"{r['key']}: {r['value']}")

    print("\n--- CURRENT SCAN STATE ---")
    css = db.execute_db("SELECT * FROM current_scan_state WHERE id=1", fetch="one")
    print(json.dumps(css, indent=2, default=str))

    print("\n--- RESUME STATE ---")
    try:
        resume = db.get_pending_resume()
        print(json.dumps(resume, indent=2, default=str) if resume else "No pending resume")
    except Exception as e:
        print("Error getting resume:", e)

    print("\n--- LATEST SCAN RUNS (Top 3) ---")
    try:
        runs = db.execute_db("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 3", fetch="all")
        for r in runs:
            print(json.dumps(r, default=str))
    except Exception as e:
        print("Error getting scan_runs:", e)

    print("\n--- RECENT SCAN TRANSITIONS (Top 5) ---")
    try:
        transitions = db.execute_db("SELECT * FROM scan_state_transitions ORDER BY id DESC LIMIT 5", fetch="all")
        for r in transitions:
            print(json.dumps(r, default=str))
    except Exception as e:
        print("Error getting transitions:", e)

    print("\n--- UNIVERSE COUNTS BY VERSION ---")
    try:
        counts = db.execute_db("SELECT universe_version, COUNT(*) as c FROM stocks GROUP BY universe_version", fetch="all")
        print(counts)
    except Exception as e:
        print("Error getting universe counts:", e)

if __name__ == "__main__":
    db.init_db()
    run_audit()
