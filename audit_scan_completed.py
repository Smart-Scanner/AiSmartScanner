import sqlite3
import json

c = sqlite3.connect('cache/screener.db')
c.row_factory = sqlite3.Row

scans = ["scan_auto_1781508493_5568c1", "scan_manual_1781856444_055982"]

for scan in scans:
    print(f"--- {scan} ---")
    rows = c.execute("SELECT event_type, details, created_at FROM scan_event_audit WHERE scan_id = ?", (scan,)).fetchall()
    
    # Let's count some events
    events = {}
    scan_completed = None
    
    for r in rows:
        events[r['event_type']] = events.get(r['event_type'], 0) + 1
        if r['event_type'] == 'SCAN_COMPLETED':
            scan_completed = dict(r)
            
    print(f"Total events: {len(rows)}")
    print(f"Event counts: {json.dumps(events, indent=2)}")
    if scan_completed:
        print(f"SCAN_COMPLETED: {scan_completed}")
    else:
        print("SCAN_COMPLETED not found.")
        
    print()

