"""
FORENSIC DEEP DIVE — Phase 3: Finalization Evidence
"""
import os, psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
url += '&sslmode=require' if '?' in url else '?sslmode=require'
SCAN_ID = 'scan_manual_1781858049_314672'
CONCURRENT = 'scan_manual_1781858997_3b2f2d'

conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
conn.autocommit = True
cur = conn.cursor()

# ================================================================
# PART A: ALL finalization events for TARGET scan
# ================================================================
print("="*70)
print("PART A: Finalization events for TARGET scan")
print("="*70)
finalization_events = [
    'FINALIZE_STARTED', 'SECTOR_STRENGTH_STARTED', 'SECTOR_STRENGTH_COMPLETED',
    'AI_SUMMARY_STARTED', 'AI_SUMMARY_COMPLETED', 
    'SAVE_RESULTS_STARTED', 'SAVE_RESULTS_COMPLETED',
    'SNAPSHOT_STARTED', 'SNAPSHOT_COMPLETED',
    'FINALIZE_COMPLETED', 'SCAN_COMPLETED', 'SCAN_FAILED'
]
for evt in finalization_events:
    cur.execute("SELECT COUNT(*) as cnt FROM scan_event_audit WHERE scan_id=%s AND event_type=%s",
                (SCAN_ID, evt))
    cnt = cur.fetchone()['cnt']
    status = "MISSING" if cnt == 0 else f"PRESENT ({cnt})"
    print(f"  {evt:35} {status}")

# ================================================================
# PART B: Same check for CONCURRENT scan
# ================================================================
print("\n" + "="*70)
print("PART B: Finalization events for CONCURRENT scan")
print("="*70)
for evt in finalization_events:
    cur.execute("SELECT COUNT(*) as cnt FROM scan_event_audit WHERE scan_id=%s AND event_type=%s",
                (CONCURRENT, evt))
    cnt = cur.fetchone()['cnt']
    status = "MISSING" if cnt == 0 else f"PRESENT ({cnt})"
    print(f"  {evt:35} {status}")

# ================================================================
# PART C: Check a SUCCESSFUL scan for comparison
# ================================================================
print("\n" + "="*70)
print("PART C: Finalization events for a SUCCESSFUL scan (comparison)")
print("="*70)
cur.execute("SELECT scan_id FROM scan_runs WHERE status='completed' ORDER BY start_time DESC LIMIT 1")
success = cur.fetchone()
if success:
    sid = success['scan_id']
    print(f"  Comparing with: {sid}")
    for evt in finalization_events:
        cur.execute("SELECT COUNT(*) as cnt FROM scan_event_audit WHERE scan_id=%s AND event_type=%s",
                    (sid, evt))
        cnt = cur.fetchone()['cnt']
        status = "MISSING" if cnt == 0 else f"PRESENT ({cnt})"
        print(f"  {evt:35} {status}")

# ================================================================
# PART D: TIMESTAMP ALIGNMENT — resolve IST vs UTC
# ================================================================
print("\n" + "="*70)
print("PART D: Timestamp alignment (IST vs UTC)")
print("="*70)
# scan_event_audit uses datetime.now() from Python (IST)
# scan_state_transitions uses datetime.now().strftime() from Python (IST) but stored as TEXT
# scan_runs uses PG TIMESTAMP which is UTC

# The scan_event_audit created_at is a PG TIMESTAMP column populated by:
# db.py:2581: INSERT INTO scan_event_audit (...) VALUES (?, ?, ?)
# The table schema at db.py:1118: created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
# BUT the INSERT does NOT specify created_at, so it uses DEFAULT = CURRENT_TIMESTAMP = UTC
# Wait, let me check the INSERT statement

# Actually from db.py:2580-2583:
# execute_db("INSERT INTO scan_event_audit (scan_id, event_type, details) VALUES (?, ?, ?)", ...)
# No created_at specified, so it uses DEFAULT CURRENT_TIMESTAMP
# PG CURRENT_TIMESTAMP is UTC
# But the output shows 08:34:14 for scan start, while scan_runs shows 14:04:10
# 14:04 - 08:34 = 5:30 = IST offset
# So scan_event_audit.created_at is in IST somehow?

# Let's check: maybe the PG server IS in UTC but the app overrides it
# Or maybe DEFAULT CURRENT_TIMESTAMP is using the session timezone

# Check if scan_state_transitions uses app-provided timestamp
# db.py:2338-2343: INSERT ... VALUES (..., ?, ...) with now = datetime.now().strftime(...)
# So scan_state_transitions.created_at comes from Python datetime.now() = IST

# And scan_runs.start_time? Let's check acquire_scan_lock
# db.py:2469: now = datetime.now().strftime(...)
# db.py:2501: VALUES (?, ?, 'running', 'init', ?, 0, ?, ...)
# So scan_runs.start_time also comes from Python datetime.now() = IST???

# But we see scan_runs.start_time = 14:04:10 and scan_event_audit = 08:34:14
# 14:04 IST would be 08:34 UTC. So scan_runs is in IST and scan_event_audit is in UTC!

# Unless... scan_runs.start_time is set via execute_db which translates ? to %s
# and PG implicitly converts the text to TIMESTAMP in UTC...

# Let's just directly compare the timestamps from scan_runs and scan_event_audit
# for SCAN_STARTED event which should be near-simultaneous

cur.execute("SELECT start_time FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
sr_start = cur.fetchone()['start_time']
cur.execute("SELECT created_at FROM scan_event_audit WHERE scan_id=%s AND event_type='SCAN_STARTED'", (SCAN_ID,))
ea_start = cur.fetchone()['created_at']
print(f"  scan_runs.start_time:              {sr_start}")
print(f"  scan_event_audit.SCAN_STARTED:     {ea_start}")
print(f"  Difference: scan_runs is {(sr_start - ea_start).total_seconds():.0f}s ahead of event_audit")
diff_hours = (sr_start - ea_start).total_seconds() / 3600
print(f"  That's {diff_hours:.1f} hours")
if abs(diff_hours - 5.5) < 0.1:
    print(f"  >>> CONFIRMED: scan_runs uses IST (Python datetime.now())")
    print(f"  >>> scan_event_audit uses UTC (PG DEFAULT CURRENT_TIMESTAMP)")
    print(f"  >>> All scan_runs timestamps = IST, all scan_event_audit timestamps = UTC")

# Now build the UNIFIED timeline in IST
print("\n" + "="*70)
print("PART E: UNIFIED TIMELINE (all times converted to IST)")
print("="*70)

# scan_runs timestamps are already IST
# scan_event_audit timestamps need +5:30
# scan_state_transitions - let's check

cur.execute("""
    SELECT created_at FROM scan_state_transitions 
    WHERE scan_id=%s AND old_state='idle' AND new_state='running'
""", (SCAN_ID,))
st_start = cur.fetchone()['created_at']
print(f"  scan_state_transitions.idle->running: {st_start}")
print(f"  scan_runs.start_time:                 {sr_start}")
diff_st = (sr_start - st_start).total_seconds() if hasattr(sr_start, '__sub__') else '?'
print(f"  Difference: {diff_st}s")
if isinstance(diff_st, (int, float)) and abs(diff_st) < 5:
    print(f"  >>> scan_state_transitions uses IST (same as scan_runs)")

# Build unified timeline
from datetime import timedelta
IST_OFFSET = timedelta(hours=5, minutes=30)

events = []

# scan_runs
events.append(('scan_runs.start_time', sr_start, 'IST'))  # IST
cur.execute("SELECT last_heartbeat FROM scan_runs WHERE scan_id=%s", (SCAN_ID,))
hb = cur.fetchone()['last_heartbeat']
events.append(('scan_runs.last_heartbeat', hb, 'IST'))

# scan_event_audit (UTC - need to add 5:30)
cur.execute("""
    SELECT event_type, created_at FROM scan_event_audit 
    WHERE scan_id=%s AND event_type NOT LIKE 'SYMBOL%%'
    ORDER BY created_at ASC
""", (SCAN_ID,))
for r in cur.fetchall():
    ist_time = r['created_at'] + IST_OFFSET
    events.append((f"event:{r['event_type']}", ist_time, 'UTC+5:30'))

# scan_state_transitions (IST)
cur.execute("""
    SELECT old_state, new_state, reason, actor, created_at 
    FROM scan_state_transitions WHERE scan_id=%s ORDER BY created_at ASC
""", (SCAN_ID,))
for r in cur.fetchall():
    events.append((f"transition:{r['old_state']}->{r['new_state']} ({r['actor']})", r['created_at'], 'IST'))

# Sort by timestamp
events.sort(key=lambda x: x[1])

print(f"\n  UNIFIED TIMELINE (IST):")
for name, ts, tz_source in events:
    print(f"    [{ts}] {name}")

# ================================================================
# PART F: THE CRITICAL 7.5 MINUTE WINDOW
# ================================================================  
print("\n" + "="*70)
print("PART F: THE CRITICAL WINDOW ANALYSIS")
print("="*70)
# chunk_completed event (UTC) = 08:40:47 -> IST = 14:10:47
# last_heartbeat (IST) = 14:18:14
# Heartbeat continued for 7m27s after chunk completed
# Then heartbeat stopped
# 15.8 min later watchdog killed it

print("  CHUNK_COMPLETED (IST): 14:10:47")
print("  last_heartbeat (IST):  14:18:14")
print("  Heartbeat gap:         7m27s (heartbeat alive AFTER all stocks done)")
print("  Watchdog kill (IST):   14:34:01")
print("  Death gap:             15m47s (from last heartbeat to watchdog)")
print()
print("  During the 7m27s finalization window, the scanner was:")
print("  - Running save_results (575 symbols x 8 tables = massive batch)")
print("  - Running sector_strength, AI summary, snapshots, execution engine")
print("  - Heartbeat thread was still pinging PG every 30s")
print()
print("  At 14:18:14, heartbeat STOPPED. This means either:")
print("  A. The main thread crashed and called heartbeat_stop.set() in except/finally")
print("  B. The heartbeat thread itself died (PG pool exhaustion)")
print("  C. The process was killed externally")
print()

# Check if FINALIZE_STARTED exists for concurrent scan
cur.execute("""
    SELECT event_type, created_at FROM scan_event_audit 
    WHERE scan_id=%s AND event_type NOT LIKE 'SYMBOL%%'
    ORDER BY created_at ASC
""", (CONCURRENT,))
print("  CONCURRENT SCAN TIMELINE (for pattern comparison):")
for r in cur.fetchall():
    ist_time = r['created_at'] + IST_OFFSET
    print(f"    [{ist_time}] {r['event_type']}")

conn.close()
