"""Remaining evidence: scan history, heartbeat, SQLite check"""
import os, json, psycopg2, sqlite3, glob
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
url += '&sslmode=require' if '?' in url else '?sslmode=require'
SCAN_ID = 'scan_manual_1781858049_314672'

conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
conn.autocommit = True
cur = conn.cursor()

print("="*70)
print("EVIDENCE I: RECENT SCAN HISTORY (last 10 scans)")
print("="*70)
cur.execute("""
    SELECT scan_id, status, error_message, start_time, end_time, 
           duration_seconds, processed_count, phase, last_heartbeat
    FROM scan_runs ORDER BY start_time DESC LIMIT 10
""")
for r in cur.fetchall():
    err = (r.get('error_message') or '')[:40]
    marker = " <<< TARGET" if r['scan_id'] == SCAN_ID else ""
    print(f"  [{r['start_time']}] {r['scan_id'][:35]} status={r['status']} "
          f"err={err} proc={r.get('processed_count','')} "
          f"phase={r.get('phase','')}{marker}")

print("\n" + "="*70)
print("EVIDENCE J: HEARTBEAT ANALYSIS")
print("="*70)
cur.execute("SELECT start_time, last_heartbeat, end_time, status, phase FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
row = cur.fetchone()
if row:
    print(f"  start_time:     {row['start_time']}")
    print(f"  last_heartbeat: {row['last_heartbeat']}")
    print(f"  end_time:       {row['end_time']}")
    print(f"  status:         {row['status']}")
    print(f"  phase:          {row['phase']}")
    
    st = row['start_time']
    hb = row['last_heartbeat']
    if st and hb:
        if isinstance(st, str): st = datetime.fromisoformat(st)
        if isinstance(hb, str): hb = datetime.fromisoformat(hb)
        if st.tzinfo: st = st.replace(tzinfo=None)
        if hb.tzinfo: hb = hb.replace(tzinfo=None)
        print(f"  heartbeat_lifetime: {(hb - st).total_seconds():.0f}s ({(hb - st).total_seconds()/60:.1f} min)")
        # chunk_completed was at 08:40:47
        chunk_done = datetime(2026, 6, 19, 8, 40, 48)
        hb_vs_chunk = (hb - chunk_done).total_seconds()
        print(f"  last_heartbeat vs chunk_completed: {hb_vs_chunk:.0f}s")
        if hb_vs_chunk < 0:
            print("  >>> HEARTBEAT DIED BEFORE CHUNK COMPLETED <<<")
        elif hb_vs_chunk < 60:
            print("  >>> HEARTBEAT DIED WITHIN 1 MIN OF CHUNK COMPLETED <<<")
        else:
            print(f"  >>> HEARTBEAT CONTINUED {hb_vs_chunk:.0f}s AFTER CHUNK <<<")

print("\n" + "="*70)
print("EVIDENCE K: SQLite CHECK")
print("="*70)
db_files = glob.glob("**/*.db", recursive=True)
print(f"  .db files found: {db_files}")
for db_path in db_files:
    print(f"\n  Checking: {db_path}")
    try:
        sconn = sqlite3.connect(db_path)
        sconn.row_factory = sqlite3.Row
        scur = sconn.cursor()
        # List tables
        scur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r['name'] for r in scur.fetchall()]
        print(f"    Tables: {tables}")
        
        for tbl in ['scan_state_transitions', 'scan_runs', 'scan_event_audit', 'current_scan_state', 'scan_lock']:
            if tbl in tables:
                scur.execute(f"SELECT COUNT(*) as cnt FROM {tbl}")
                cnt = scur.fetchone()['cnt']
                print(f"    {tbl}: {cnt} rows total")
                if cnt > 0:
                    scur.execute(f"SELECT * FROM {tbl} WHERE scan_id=?", (SCAN_ID,))
                    rows = scur.fetchall()
                    print(f"    {tbl} rows for TARGET scan: {len(rows)}")
                    for r in rows:
                        print(f"      {dict(r)}")
        sconn.close()
    except Exception as e:
        print(f"    Error: {e}")

print("\n" + "="*70)
print("EVIDENCE L: SCAN_META")
print("="*70)
cur.execute("SELECT * FROM scan_meta ORDER BY key")
for r in cur.fetchall():
    k = r.get('key', r.get('meta_key', '???'))
    v = r.get('value', r.get('meta_value', '???'))
    print(f"  {k}: {str(v)[:80]}")

# CRITICAL: Check for the SECOND scan that started WHILE our target was still "running"
print("\n" + "="*70)
print("EVIDENCE M: CONCURRENT SCAN DETECTION")
print("="*70)
cur.execute("""
    SELECT scan_id, status, start_time, end_time
    FROM scan_runs 
    WHERE start_time > '2026-06-19 14:04:00' AND start_time < '2026-06-19 14:35:00'
    ORDER BY start_time ASC
""")
for r in cur.fetchall():
    marker = " <<< TARGET" if r['scan_id'] == SCAN_ID else " <<< CONCURRENT"
    print(f"  [{r['start_time']}] {r['scan_id'][:35]} status={r['status']}{marker}")

conn.close()
