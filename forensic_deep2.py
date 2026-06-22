"""
FORENSIC DEEP DIVE — Phase 2
scan_manual_1781858049_314672

Pure evidence. No fixes. No recommendations.
"""
import os, psycopg2, sqlite3, glob
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
url += '&sslmode=require' if '?' in url else '?sslmode=require'
SCAN_ID = 'scan_manual_1781858049_314672'
CONCURRENT_SCAN = 'scan_manual_1781858997_3b2f2d'

conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
conn.autocommit = True
cur = conn.cursor()

# ================================================================
# PART 1: TIMEZONE RESOLUTION
# scan_event_audit appears to use IST, scan_runs uses UTC
# Need to determine actual server timezone
# ================================================================
print("="*70)
print("PART 1: TIMEZONE RESOLUTION")
print("="*70)
cur.execute("SHOW timezone")
tz = cur.fetchone()
print(f"  PostgreSQL server timezone: {tz}")
cur.execute("SELECT NOW() as now, CURRENT_TIMESTAMP as ts")
row = cur.fetchone()
print(f"  PG NOW(): {row['now']}")

# Compare scan_runs.start_time with first event
cur.execute("SELECT start_time, last_heartbeat FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
sr = cur.fetchone()
cur.execute("SELECT MIN(created_at) as first_evt FROM scan_event_audit WHERE scan_id=%s", (SCAN_ID,))
fe = cur.fetchone()
print(f"  scan_runs.start_time:       {sr['start_time']} (type={type(sr['start_time']).__name__})")
print(f"  scan_event_audit.first_evt: {fe['first_evt']} (type={type(fe['first_evt']).__name__})")
print(f"  scan_runs.last_heartbeat:   {sr['last_heartbeat']}")

# ================================================================
# PART 2: PRECISE TIMELINE CONSTRUCTION
# ================================================================
print("\n" + "="*70)
print("PART 2: PRECISE TIMELINE")
print("="*70)

# Get all events with their raw timestamps
cur.execute("""
    SELECT 'event' as source, event_type as event, details, created_at
    FROM scan_event_audit WHERE scan_id=%s
    UNION ALL
    SELECT 'transition' as source, 
           old_state || ' -> ' || new_state as event,
           'reason=' || reason || ' actor=' || actor as details,
           created_at
    FROM scan_state_transitions WHERE scan_id=%s
    ORDER BY created_at ASC
""", (SCAN_ID, SCAN_ID))
all_events = cur.fetchall()

# Also get scan_runs timestamps
print(f"\n  scan_runs timestamps:")
print(f"    start_time:     {sr['start_time']}")
print(f"    last_heartbeat: {sr['last_heartbeat']}")

# Key moments
print(f"\n  Complete event timeline ({len(all_events)} events):")
last_symbol = None
chunk_complete_time = None
for e in all_events:
    tag = ""
    if e['event'] == 'SCAN_STARTED':
        tag = " *** SCAN START ***"
    elif e['event'] == 'CHUNK_COMPLETED':
        tag = " *** ALL STOCKS DONE ***"
        chunk_complete_time = e['created_at']
    elif 'zombie' in str(e['event']).lower():
        tag = " *** WATCHDOG ***"
    elif 'failed' in str(e['event']).lower() and e['source'] == 'transition':
        tag = " *** STATE CHANGE ***"
    elif e['event'] == 'SCAN_FAILED':
        tag = " *** SCANNER CRASH ***"
    elif e['event'] == 'SCAN_COMPLETED':
        tag = " *** SCANNER SUCCESS ***"
    
    # Only print non-SYMBOL_FAILED events to keep it clean
    if e['event'] != 'SYMBOL_FAILED':
        det = (e['details'] or '')[:80]
        print(f"    [{e['created_at']}] [{e['source']:10}] {e['event']}: {det}{tag}")
    elif last_symbol != e['event']:
        last_symbol = e['event']

# Show last 3 SYMBOL_FAILED for context
cur.execute("""
    SELECT event_type, details, created_at FROM scan_event_audit 
    WHERE scan_id=%s AND event_type='SYMBOL_FAILED'
    ORDER BY created_at DESC LIMIT 3
""", (SCAN_ID,))
print(f"\n  Last 3 SYMBOL_FAILED events:")
for r in cur.fetchall():
    print(f"    [{r['created_at']}] {r['details']}")

# ================================================================
# PART 3: THE GAP ANALYSIS
# ================================================================
print("\n" + "="*70)
print("PART 3: THE GAP — What happened between chunk_done and watchdog?")
print("="*70)

if chunk_complete_time and sr['last_heartbeat']:
    ct = chunk_complete_time
    hb = sr['last_heartbeat']
    if isinstance(ct, str): ct = datetime.fromisoformat(ct)
    if isinstance(hb, str): hb = datetime.fromisoformat(hb)
    # Strip timezone for safe subtraction
    if ct.tzinfo: ct = ct.replace(tzinfo=None)
    if hb.tzinfo: hb = hb.replace(tzinfo=None)
    
    print(f"  CHUNK_COMPLETED:  {chunk_complete_time}")
    print(f"  last_heartbeat:   {sr['last_heartbeat']}")
    
    gap_hb = (hb - ct).total_seconds()
    print(f"  Heartbeat continued for: {gap_hb:.0f}s ({gap_hb/60:.1f}min) after chunk completed")
    
    if gap_hb > 0:
        print(f"  >>> Scanner was ALIVE for {gap_hb:.0f}s after processing finished")
        print(f"  >>> This is the FINALIZATION window (save_results, AI summary, etc)")
    elif gap_hb < 0:
        print(f"  >>> WARNING: Heartbeat died BEFORE chunk completed (timezone issue?)")

# ================================================================
# PART 4: CONCURRENT SCAN — CRITICAL EVIDENCE
# ================================================================
print("\n" + "="*70)
print("PART 4: CONCURRENT SCAN INVESTIGATION")
print("="*70)
print(f"  Target scan:     {SCAN_ID}")
print(f"  Concurrent scan: {CONCURRENT_SCAN}")

cur.execute("SELECT * FROM scan_runs WHERE scan_id=%s", (CONCURRENT_SCAN,))
cr = cur.fetchone()
if cr:
    print(f"\n  Concurrent scan details:")
    print(f"    start_time:     {cr['start_time']}")
    print(f"    last_heartbeat: {cr['last_heartbeat']}")
    print(f"    end_time:       {cr['end_time']}")
    print(f"    status:         {cr['status']}")
    print(f"    error_message:  {cr.get('error_message', '')}")
    print(f"    processed_count:{cr.get('processed_count', '')}")
    print(f"    phase:          {cr.get('phase', '')}")
    
    # CRITICAL: This scan started at 14:19:58 while our target was still "running" in PG
    # How did it acquire the lock?
    print(f"\n  >>> CRITICAL QUESTION: Our target scan was still 'running' in PG at 14:19:58")
    print(f"  >>> How did {CONCURRENT_SCAN} acquire the scan lock?")
    print(f"  >>> Possible answers:")
    print(f"  >>>   A. Lock acquisition read from SQLite (where target was not running)")
    print(f"  >>>   B. Target scan had already released its lock before crash")
    print(f"  >>>   C. Lock acquisition bypassed status check")

# Check concurrent scan's transitions
cur.execute("""
    SELECT old_state, new_state, reason, actor, created_at
    FROM scan_state_transitions WHERE scan_id=%s
    ORDER BY created_at ASC
""", (CONCURRENT_SCAN,))
print(f"\n  Concurrent scan transitions:")
for r in cur.fetchall():
    print(f"    [{r['created_at']}] {r['old_state']}->{r['new_state']} reason={r['reason']} actor={r['actor']}")

# Check concurrent scan events (non-symbol)
cur.execute("""
    SELECT event_type, details, created_at FROM scan_event_audit
    WHERE scan_id=%s AND event_type NOT LIKE 'SYMBOL%%'
    ORDER BY created_at ASC
""", (CONCURRENT_SCAN,))
print(f"\n  Concurrent scan events (non-symbol):")
for r in cur.fetchall():
    print(f"    [{r['created_at']}] {r['event_type']}: {(r['details'] or '')[:80]}")

# ================================================================
# PART 5: SQLITE FALLBACK TELEMETRY
# ================================================================
print("\n" + "="*70)
print("PART 5: SQLite FALLBACK TELEMETRY")
print("="*70)
cur.execute("SELECT value FROM scan_meta WHERE key='sqlite_fallback_count'")
row = cur.fetchone()
print(f"  sqlite_fallback_count: {row['value'] if row else 'NOT FOUND'}")

# Check if there are counters in scan_meta for db errors
for key in ['db_failures', 'db_pool_exhausted', 'db_queries', 'sqlite_fallback_used']:
    cur.execute("SELECT value FROM scan_meta WHERE key=%s", (key,))
    row = cur.fetchone()
    if row:
        print(f"  {key}: {row['value']}")

# ================================================================
# PART 6: MISSING EVENTS ANALYSIS
# ================================================================
print("\n" + "="*70)
print("PART 6: MISSING EVENTS — What SHOULD exist but doesn't")
print("="*70)

# Events that scanner.py emits during finalization
expected_events = [
    'SCAN_COMPLETED',    # scanner.py:948
    'SCAN_FAILED',       # scanner.py:952
    'SAVE_RESULTS_STARTED',  # if exists
    'SAVE_RESULTS_COMPLETED',# if exists
]
for evt in expected_events:
    cur.execute("SELECT COUNT(*) as cnt FROM scan_event_audit WHERE scan_id=%s AND event_type=%s", (SCAN_ID, evt))
    cnt = cur.fetchone()['cnt']
    status = "MISSING <<<" if cnt == 0 else f"PRESENT ({cnt})"
    print(f"  {evt}: {status}")

# ================================================================
# PART 7: LOCK STATE AT TIME OF CONCURRENT SCAN START
# ================================================================
print("\n" + "="*70)
print("PART 7: LOCK ACQUISITION PATH ANALYSIS")
print("="*70)

# The acquire_scan_lock function does:
# UPDATE current_scan_state SET status='running' WHERE id=1 AND status != 'running'
# If this went through, it means current_scan_state was NOT 'running' at 14:19:58

# Check: what was current_scan_state showing?
# We can infer from the transition that succeeded:
# idle -> running for concurrent scan at 14:19:58
# This means current_scan_state was showing 'idle' at that moment

print(f"  At 14:19:58, concurrent scan acquired lock via:")
print(f"    UPDATE current_scan_state SET status='running' WHERE status != 'running'")
print(f"  This SUCCEEDED (rowcount=1), which means current_scan_state.status was NOT 'running'")
print(f"")
print(f"  But our target scan was still 'running' in scan_runs (PG confirmed)")
print(f"  This proves ONE of:")
print(f"    1. current_scan_state was reset to 'idle' by a finalization path")
print(f"    2. current_scan_state was read from SQLite where it showed 'idle'")
print(f"    3. _sync_current_scan_state executed successfully for a terminal transition")

# Check the SQLite current_scan_state
print(f"\n  Checking SQLite current_scan_state:")
try:
    sconn = sqlite3.connect('cache/screener.db')
    sconn.row_factory = sqlite3.Row
    scur = sconn.cursor()
    scur.execute("SELECT * FROM current_scan_state WHERE id=1")
    row = scur.fetchone()
    if row:
        print(f"    SQLite current_scan_state: {dict(row)}")
    sconn.close()
except Exception as e:
    print(f"    Error: {e}")

# ================================================================
# PART 8: SCAN THAT SUCCEEDED BEFORE TARGET
# ================================================================
print("\n" + "="*70)
print("PART 8: SCANS THAT COMPLETED JUST BEFORE TARGET")
print("="*70)
cur.execute("""
    SELECT scan_id, status, start_time, end_time, duration_seconds, processed_count
    FROM scan_runs 
    WHERE start_time < '2026-06-19 14:04:10' AND start_time > '2026-06-19 13:30:00'
    ORDER BY start_time ASC
""")
for r in cur.fetchall():
    print(f"  [{r['start_time']}] {r['scan_id'][:35]} status={r['status']} "
          f"dur={r.get('duration_seconds','')}s proc={r.get('processed_count','')}")

# Did those scans release resources properly? Check if they completed before our scan started
cur.execute("""
    SELECT scan_id, end_time FROM scan_runs 
    WHERE end_time > '2026-06-19 14:00:00' AND start_time < '2026-06-19 14:04:00'
    ORDER BY end_time ASC
""")
late_completions = cur.fetchall()
if late_completions:
    print(f"\n  Scans that ended AFTER 14:00 (potentially overlapping):")
    for r in late_completions:
        print(f"    {r['scan_id'][:35]} end_time={r['end_time']}")

# ================================================================  
# PART 9: ALL SCANS WITH HEARTBEAT TIMEOUT (PATTERN ANALYSIS)
# ================================================================
print("\n" + "="*70)
print("PART 9: ALL 'Heartbeat timeout' FAILURES — Pattern")
print("="*70)
cur.execute("""
    SELECT scan_id, status, start_time, end_time, last_heartbeat, 
           processed_count, phase, error_message
    FROM scan_runs 
    WHERE error_message LIKE '%%Heartbeat%%'
    ORDER BY start_time DESC
    LIMIT 10
""")
rows = cur.fetchall()
print(f"  Total heartbeat timeout failures found: {len(rows)}")
for r in rows:
    st = r['start_time']
    hb = r['last_heartbeat']
    if st and hb:
        if isinstance(st, str): st = datetime.fromisoformat(st)
        if isinstance(hb, str): hb = datetime.fromisoformat(hb)
        if st.tzinfo: st = st.replace(tzinfo=None)
        if hb.tzinfo: hb = hb.replace(tzinfo=None)
        hb_life = (hb - st).total_seconds() / 60
    else:
        hb_life = '?'
    marker = " <<< TARGET" if r['scan_id'] == SCAN_ID else ""
    print(f"  [{r['start_time']}] {r['scan_id'][:35]} proc={r.get('processed_count','')} "
          f"phase={r.get('phase','')} hb_life={hb_life}min{marker}")

conn.close()
