"""
FORENSIC EVIDENCE EXTRACTION
scan_manual_1781858049_314672

READ-ONLY. NO CHANGES.
Extracts all available production evidence from PostgreSQL.
"""
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

url = os.getenv('DATABASE_URL')
url = url.replace('postgres://', 'postgresql://')
url += '&sslmode=require' if '?' in url else '?sslmode=require'

SCAN_ID = 'scan_manual_1781858049_314672'

def run():
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    conn.autocommit = True
    cur = conn.cursor()

    print("="*70)
    print("EVIDENCE A: SCAN_RUNS — Full Row")
    print("="*70)
    cur.execute("SELECT * FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
    row = cur.fetchone()
    if row:
        for k, v in row.items():
            print(f"  {k}: {v}")
    else:
        print("  NOT FOUND")

    print("\n" + "="*70)
    print("EVIDENCE B: CURRENT_SCAN_STATE — Singleton Row")
    print("="*70)
    cur.execute("SELECT * FROM current_scan_state WHERE id=1")
    row = cur.fetchone()
    if row:
        for k, v in row.items():
            print(f"  {k}: {v}")

    print("\n" + "="*70)
    print("EVIDENCE C: SCAN_STATE_TRANSITIONS — Full Chain")
    print("="*70)
    cur.execute("""
        SELECT id, scan_id, old_state, new_state, reason, actor, correlation_id, hash_chain, created_at
        FROM scan_state_transitions
        WHERE scan_id=%s
        ORDER BY created_at ASC
    """, (SCAN_ID,))
    rows = cur.fetchall()
    print(f"  Total transitions: {len(rows)}")
    for r in rows:
        print(f"  [{r['created_at']}] {r['old_state']} -> {r['new_state']} | reason={r['reason']} | actor={r['actor']}")

    # Check: is there a 'failed' transition from the scanner (not watchdog)?
    scanner_failed = [r for r in rows if r['new_state'] == 'failed' and r['actor'] != 'watchdog']
    watchdog_failed = [r for r in rows if r['new_state'] in ('failed', 'zombie_detected') and r['actor'] == 'watchdog']
    print(f"\n  Scanner 'failed' transitions: {len(scanner_failed)}")
    print(f"  Watchdog 'failed' transitions: {len(watchdog_failed)}")

    if not scanner_failed:
        print("  >>> EVIDENCE: Scanner's finally-block transition is MISSING from PG <<<")

    print("\n" + "="*70)
    print("EVIDENCE D: SCAN_EVENT_AUDIT — Full Event Log")
    print("="*70)
    cur.execute("""
        SELECT id, scan_id, event_type, details, created_at
        FROM scan_event_audit
        WHERE scan_id=%s
        ORDER BY created_at ASC
    """, (SCAN_ID,))
    events = cur.fetchall()
    print(f"  Total events: {len(events)}")

    last_processing_event = None
    first_watchdog_event = None
    for e in events:
        tag = ""
        if e['event_type'] in ('ZOMBIE_DETECTED', 'WATCHDOG_RECOVERY_STARTED', 'WATCHDOG_RECOVERY_COMPLETED'):
            tag = " <<< WATCHDOG"
            if first_watchdog_event is None:
                first_watchdog_event = e
        elif e['event_type'] in ('CHUNK_COMPLETED',):
            tag = " <<< LAST PROCESSING"
            last_processing_event = e
        elif e['event_type'] == 'SCAN_FAILED':
            tag = " <<< SCANNER ERROR"
        elif e['event_type'] == 'SCAN_COMPLETED':
            tag = " <<< SCANNER SUCCESS"
        print(f"  [{e['created_at']}] {e['event_type']} | {e['details']}{tag}")

    # Calculate the gap
    if last_processing_event and first_watchdog_event:
        t1 = last_processing_event['created_at']
        t2 = first_watchdog_event['created_at']
        if isinstance(t1, str):
            t1 = datetime.fromisoformat(t1)
        if isinstance(t2, str):
            t2 = datetime.fromisoformat(t2)
        gap = (t2 - t1).total_seconds() / 60
        print(f"\n  >>> GAP: {gap:.1f} minutes between last processing and watchdog detection <<<")

    # Check: is SCAN_FAILED event present?
    scan_failed_events = [e for e in events if e['event_type'] == 'SCAN_FAILED']
    scan_completed_events = [e for e in events if e['event_type'] == 'SCAN_COMPLETED']
    print(f"\n  SCAN_FAILED events: {len(scan_failed_events)}")
    print(f"  SCAN_COMPLETED events: {len(scan_completed_events)}")

    if not scan_failed_events and not scan_completed_events:
        print("  >>> EVIDENCE: Neither SCAN_FAILED nor SCAN_COMPLETED event exists <<<")
        print("  >>> This means the scanner crashed AFTER phase1 but BEFORE reaching the")
        print("  >>> except block (line 950) OR the log_scan_event call itself was routed to SQLite <<<")

    print("\n" + "="*70)
    print("EVIDENCE E: SCAN_LOCK — Current State")
    print("="*70)
    cur.execute("SELECT * FROM scan_lock WHERE id=1")
    row = cur.fetchone()
    if row:
        for k, v in row.items():
            print(f"  {k}: {v}")

    print("\n" + "="*70)
    print("EVIDENCE F: SCAN_RESUME_STATE")
    print("="*70)
    cur.execute("SELECT * FROM scan_resume_state WHERE scan_id=%s", (SCAN_ID,))
    row = cur.fetchone()
    if row:
        for k, v in row.items():
            print(f"  {k}: {v}")
    else:
        print("  NOT FOUND (scan may not have used resume checkpoints)")

    print("\n" + "="*70)
    print("EVIDENCE G: ALL STATE TRANSITIONS AROUND THE TIME WINDOW")
    print("="*70)
    # Get the scan start time first
    cur.execute("SELECT start_time FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
    sr = cur.fetchone()
    if sr and sr.get('start_time'):
        st = sr['start_time']
        if isinstance(st, str):
            st = datetime.fromisoformat(st)
        window_start = st - timedelta(minutes=5)
        window_end = st + timedelta(minutes=40)
        cur.execute("""
            SELECT id, scan_id, old_state, new_state, reason, actor, created_at
            FROM scan_state_transitions
            WHERE created_at BETWEEN %s AND %s
            ORDER BY created_at ASC
        """, (window_start.strftime("%Y-%m-%d %H:%M:%S"), window_end.strftime("%Y-%m-%d %H:%M:%S")))
        rows = cur.fetchall()
        print(f"  All transitions in window ({window_start} to {window_end}):")
        for r in rows:
            marker = " <<<" if r['scan_id'] == SCAN_ID else ""
            print(f"  [{r['created_at']}] {r['old_state']}->{r['new_state']} scan={r['scan_id'][:30]} actor={r['actor']}{marker}")

    print("\n" + "="*70)
    print("EVIDENCE H: ALL EVENTS AROUND THE TIME WINDOW")
    print("="*70)
    if sr and sr.get('start_time'):
        cur.execute("""
            SELECT id, scan_id, event_type, details, created_at
            FROM scan_event_audit
            WHERE created_at BETWEEN %s AND %s
            AND scan_id != %s
            ORDER BY created_at ASC
        """, (window_start.strftime("%Y-%m-%d %H:%M:%S"), window_end.strftime("%Y-%m-%d %H:%M:%S"), SCAN_ID))
        rows = cur.fetchall()
        print(f"  Other scan events in window: {len(rows)}")
        for r in rows[:20]:
            print(f"  [{r['created_at']}] {r['event_type']} scan={r['scan_id'][:30]} | {r['details'][:80]}")

    print("\n" + "="*70)
    print("EVIDENCE I: RECENT SCAN HISTORY (last 10 scans)")
    print("="*70)
    cur.execute("""
        SELECT scan_id, status, error_message, start_time, end_time, 
               duration_seconds, processed_count, phase, last_heartbeat
        FROM scan_runs
        ORDER BY start_time DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    for r in rows:
        marker = " <<< TARGET" if r['scan_id'] == SCAN_ID else ""
        print(f"  [{r['start_time']}] {r['scan_id'][:35]} status={r['status']} "
              f"error={r.get('error_message','')[:40]} processed={r.get('processed_count','')} "
              f"phase={r.get('phase','')}{marker}")

    print("\n" + "="*70)
    print("EVIDENCE J: HEARTBEAT ANALYSIS")
    print("="*70)
    cur.execute("SELECT start_time, last_heartbeat, end_time, status FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
    row = cur.fetchone()
    if row:
        start = row['start_time']
        hb = row['last_heartbeat']
        end = row['end_time']
        print(f"  start_time:     {start}")
        print(f"  last_heartbeat: {hb}")
        print(f"  end_time:       {end}")
        print(f"  status:         {row['status']}")
        
        if start and hb:
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            if isinstance(hb, str):
                hb = datetime.fromisoformat(hb)
            hb_age = (hb - start).total_seconds() / 60
            print(f"  heartbeat_age_from_start: {hb_age:.1f} minutes")

            # Compare last_heartbeat to last CHUNK_COMPLETED event
            if last_processing_event:
                lpe_time = last_processing_event['created_at']
                if isinstance(lpe_time, str):
                    lpe_time = datetime.fromisoformat(lpe_time)
                hb_vs_chunk = (hb - lpe_time).total_seconds()
                print(f"  last_heartbeat vs last_chunk_completed: {hb_vs_chunk:.0f} seconds")
                if hb_vs_chunk < 0:
                    print("  >>> EVIDENCE: Heartbeat STOPPED BEFORE last chunk completed <<<")
                elif hb_vs_chunk < 60:
                    print("  >>> EVIDENCE: Heartbeat stopped very close to chunk completion <<<")
                else:
                    print(f"  >>> Heartbeat continued {hb_vs_chunk:.0f}s after last chunk <<<")

    print("\n" + "="*70)
    print("EVIDENCE K: SQLite STATE TABLE CHECK")
    print("="*70)
    # Check if any state data for this scan exists in SQLite
    import sqlite3
    db_candidates = ['smart_scanner.db', 'data/smart_scanner.db']
    for db_path in db_candidates:
        if not os.path.exists(db_path):
            continue
        print(f"  Checking SQLite: {db_path}")
        try:
            sconn = sqlite3.connect(db_path)
            sconn.row_factory = sqlite3.Row
            scur = sconn.cursor()
            
            # Check scan_state_transitions
            try:
                scur.execute("SELECT * FROM scan_state_transitions WHERE scan_id=?", (SCAN_ID,))
                srows = scur.fetchall()
                print(f"    scan_state_transitions rows: {len(srows)}")
                for sr in srows:
                    print(f"      [{sr['created_at']}] {sr['old_state']}->{sr['new_state']} reason={sr['reason']}")
            except Exception as e:
                print(f"    scan_state_transitions: {e}")

            # Check scan_runs
            try:
                scur.execute("SELECT scan_id, status, error_message FROM scan_runs WHERE scan_id=?", (SCAN_ID,))
                srows = scur.fetchall()
                print(f"    scan_runs rows: {len(srows)}")
                for sr in srows:
                    print(f"      scan_id={sr['scan_id'][:30]} status={sr['status']} error={sr['error_message']}")
            except Exception as e:
                print(f"    scan_runs: {e}")

            # Check scan_event_audit
            try:
                scur.execute("SELECT * FROM scan_event_audit WHERE scan_id=?", (SCAN_ID,))
                srows = scur.fetchall()
                print(f"    scan_event_audit rows: {len(srows)}")
                for sr in srows:
                    print(f"      [{sr['created_at']}] {sr['event_type']} | {sr['details']}")
            except Exception as e:
                print(f"    scan_event_audit: {e}")

            sconn.close()
        except Exception as e:
            print(f"    Error: {e}")

    # Check if any .db file exists anywhere
    import glob
    db_files = glob.glob("**/*.db", recursive=True)
    print(f"\n  All .db files found: {db_files}")

    print("\n" + "="*70)
    print("EVIDENCE L: SCAN_META — PG Cooldown Evidence")  
    print("="*70)
    cur.execute("SELECT * FROM scan_meta ORDER BY key")
    rows = cur.fetchall()
    for r in rows:
        print(f"  {r.get('key', r.get('meta_key', '???'))}: {r.get('value', r.get('meta_value', '???'))}")

    conn.close()

if __name__ == "__main__":
    run()
