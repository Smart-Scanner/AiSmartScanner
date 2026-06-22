import os
import json
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

scan_id = 'scan_manual_1781858049_314672'

print("="*60)
print("EVIDENCE 1: scan_state_transitions")
print("="*60)

url = os.getenv('DATABASE_URL')
url = url.replace('postgres://', 'postgresql://')
url += '&sslmode=require' if '?' in url else '?sslmode=require'

try:
    with psycopg2.connect(url, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT created_at, old_state, new_state, reason, actor
                FROM scan_state_transitions
                WHERE scan_id=%s
                ORDER BY created_at ASC;
            """, (scan_id,))
            rows = cur.fetchall()
            print("PG Transitions:")
            for r in rows:
                print(f"[{r['created_at']}] {r['old_state']} -> {r['new_state']} (Reason: {r['reason']})")
except Exception as e:
    print("PG Error:", e)

# Use correct path
db_path = os.getenv("DB_PATH", "data/smart_scanner.db")
try:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT created_at, old_state, new_state, reason, actor
            FROM scan_state_transitions
            WHERE scan_id=?
            ORDER BY created_at ASC;
        """, (scan_id,))
        rows = cur.fetchall()
        print("\nSQLite Transitions:")
        for r in rows:
             print(f"[{r['created_at']}] {r['old_state']} -> {r['new_state']} (Reason: {r['reason']})")
except Exception as e:
    print("SQLite Error:", e)

print("\n"+"="*60)
print("EVIDENCE 2: scan_event_audit")
print("="*60)

try:
    with psycopg2.connect(url, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT created_at, event_type, details
                FROM scan_event_audit
                WHERE scan_id=%s
                ORDER BY created_at ASC;
            """, (scan_id,))
            rows = cur.fetchall()
            print("PG Events:")
            for r in rows:
                print(f"[{r['created_at']}] {r['event_type']} - {r['details']}")
except Exception as e:
    print("PG Error:", e)

try:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT created_at, event_type, details
            FROM scan_event_audit
            WHERE scan_id=?
            ORDER BY created_at ASC;
        """, (scan_id,))
        rows = cur.fetchall()
        print("\nSQLite Events:")
        for r in rows:
             print(f"[{r['created_at']}] {r['event_type']} - {r['details']}")
except Exception as e:
    print("SQLite Error:", e)
