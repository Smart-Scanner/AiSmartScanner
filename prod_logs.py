from dotenv import load_dotenv
load_dotenv()
import db

try:
    scan = db.execute_db('SELECT scan_id, status, started_at FROM scan_runs ORDER BY started_at DESC LIMIT 1', fetch='one')
    if not scan:
        print('No scans found in DB.')
        exit(0)

    scan_id = scan['scan_id']
    print(f"Latest Scan ID: {scan_id} | Status: {scan['status']} | Started: {scan['started_at']}")

    print('\n--- Chunk Runs ---')
    chunks = db.execute_db('SELECT chunk_name, status, symbol_count, symbols_processed, error_message FROM universe_chunk_runs WHERE scan_id = ? ORDER BY started_at', (scan_id,), fetch='all')
    for c in chunks:
        print(f"{c['chunk_name']}: {c['status']} ({c['symbols_processed']}/{c['symbol_count']}) - {c['error_message'] or ''}")

    print('\n--- Recent Scan Events ---')
    # Use scan_event_audit instead of scan_events if scan_events doesn't exist
    try:
        events = db.execute_db('SELECT event_type, details, created_at FROM scan_event_audit WHERE scan_id = ? ORDER BY created_at DESC LIMIT 20', (scan_id,), fetch='all')
        for e in events:
            print(f"[{e['created_at']}] {e['event_type']}: {e['details']}")
    except Exception as e:
        print('Failed to fetch events from scan_event_audit:', e)
except Exception as e:
    print('Error:', e)
