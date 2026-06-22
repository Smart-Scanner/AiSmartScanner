"""
FORENSIC: Check data_quality_abort transition evidence in PG
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
# Check 1: Does the transition contain a data_quality_abort reason?
# ================================================================
print("="*70)
print("CHECK 1: Transition reasons for both scans")
print("="*70)
for sid in [SCAN_ID, CONCURRENT]:
    cur.execute("""
        SELECT old_state, new_state, reason, actor, created_at
        FROM scan_state_transitions WHERE scan_id=%s ORDER BY created_at ASC
    """, (sid,))
    print(f"\n  {sid[:35]}:")
    for r in cur.fetchall():
        print(f"    [{r['created_at']}] {r['old_state']}->{r['new_state']} "
              f"reason={r['reason']} actor={r['actor']}")

# ================================================================
# Check 2: What is the error_message in scan_runs?
# ================================================================
print("\n" + "="*70)
print("CHECK 2: scan_runs error_message")
print("="*70)
for sid in [SCAN_ID, CONCURRENT]:
    cur.execute("SELECT error_message, status, phase FROM scan_runs WHERE scan_id=%s", (sid,))
    r = cur.fetchone()
    if r:
        print(f"  {sid[:35]}: error={r['error_message']} status={r['status']} phase={r['phase']}")

# ================================================================
# Check 3: Does scan_event_audit have data_quality events?
# ================================================================
print("\n" + "="*70)
print("CHECK 3: data_quality events in scan_event_audit")
print("="*70)
for sid in [SCAN_ID, CONCURRENT]:
    cur.execute("""
        SELECT event_type, details, created_at FROM scan_event_audit
        WHERE scan_id=%s AND (event_type LIKE '%%quality%%' OR details LIKE '%%quality%%' OR details LIKE '%%abort%%')
        ORDER BY created_at ASC
    """, (sid,))
    rows = cur.fetchall()
    print(f"  {sid[:35]}: {len(rows)} events")
    for r in rows:
        print(f"    [{r['created_at']}] {r['event_type']}: {r['details'][:80]}")

# ================================================================
# Check 4: Check CONCURRENT scan's event timeline fully
# ================================================================
print("\n" + "="*70)
print("CHECK 4: Concurrent scan - ALL non-symbol events") 
print("="*70)
cur.execute("""
    SELECT event_type, details, created_at FROM scan_event_audit
    WHERE scan_id=%s AND event_type NOT LIKE 'SYMBOL%%'
    ORDER BY created_at ASC
""", (CONCURRENT,))
rows = cur.fetchall()
from datetime import timedelta
IST_OFFSET = timedelta(hours=5, minutes=30)
for r in rows:
    ist = r['created_at'] + IST_OFFSET
    print(f"  [{ist}] {r['event_type']}: {(r['details'] or '')[:60]}")

# ================================================================
# Check 5: Count of SYMBOL_FAILED vs SYMBOL_COMPLETED for concurrent
# ================================================================
print("\n" + "="*70)
print("CHECK 5: Symbol outcomes for concurrent scan")
print("="*70)
for sid in [SCAN_ID, CONCURRENT]:
    cur.execute("""
        SELECT event_type, COUNT(*) as cnt FROM scan_event_audit
        WHERE scan_id=%s AND event_type IN ('SYMBOL_FAILED', 'SYMBOL_COMPLETED')
        GROUP BY event_type
    """, (sid,))
    rows = cur.fetchall()
    print(f"  {sid[:35]}:")
    for r in rows:
        print(f"    {r['event_type']}: {r['cnt']}")

# ================================================================  
# Check 6: Check scan_results for any results from these scans
# (if save_results was called before death)
# ================================================================
print("\n" + "="*70)
print("CHECK 6: scan_results existence check")
print("="*70)
cur.execute("SELECT COUNT(*) as cnt FROM scan_results")
total = cur.fetchone()['cnt']
print(f"  Total scan_results rows: {total}")
cur.execute("SELECT scan_date, COUNT(*) as cnt FROM scan_results GROUP BY scan_date ORDER BY scan_date DESC LIMIT 5")
for r in cur.fetchall():
    print(f"  scan_date={r['scan_date']}: {r['cnt']} results")

# ================================================================
# Check 7: chunk_run_id analysis - was save_results called in chunk finally?
# ================================================================
print("\n" + "="*70)
print("CHECK 7: universe_chunk_runs for both scans")
print("="*70)
for sid in [SCAN_ID, CONCURRENT]:
    cur.execute("""
        SELECT chunk_name, status, processed_count, error_details, started_at, ended_at
        FROM universe_chunk_runs WHERE scan_id=%s ORDER BY started_at ASC
    """, (sid,))
    rows = cur.fetchall()
    print(f"  {sid[:35]}: {len(rows)} chunks")
    for r in rows:
        print(f"    {r['chunk_name']} status={r['status']} proc={r['processed_count']} "
              f"err={r.get('error_details','')[:40]} "
              f"start={r['started_at']} end={r['ended_at']}")

conn.close()
