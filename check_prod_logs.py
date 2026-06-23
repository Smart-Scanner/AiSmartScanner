import db
import json

scan = db.execute_db('SELECT scan_id, status, created_at FROM scan_state ORDER BY created_at DESC LIMIT 1', fetch='one')
if not scan:
    print('No scans found in DB.')
    exit(0)

scan_id = scan['scan_id']
print(f"Latest Scan ID: {scan_id} | Status: {scan['status']} | Created: {scan['created_at']}")

print('\n--- Chunk Runs ---')
chunks = db.execute_db('SELECT chunk_name, status, symbol_count, symbols_processed, error_message FROM universe_chunk_runs WHERE scan_id = ? ORDER BY started_at', (scan_id,), fetch='all')
for c in chunks:
    print(f"{c['chunk_name']}: {c['status']} ({c['symbols_processed']}/{c['symbol_count']}) - {c['error_message'] or ''}")

print('\n--- Recent Scan Events ---')
events = db.execute_db('SELECT event_type, message, created_at FROM scan_events WHERE scan_id = ? ORDER BY created_at DESC LIMIT 20', (scan_id,), fetch='all')
for e in events:
    print(f"[{e['created_at']}] {e['event_type']}: {e['message']}")
