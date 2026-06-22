import os
import psycopg2
from dotenv import load_dotenv
import json

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url, connect_timeout=10)
cur = conn.cursor()

cur.execute("SELECT scan_id, status, error_message, last_heartbeat FROM scan_runs WHERE status = 'failed' AND error_message LIKE '%Heartbeat timeout%'")
zombies = cur.fetchall()

print("ZOMBIES IN PG:")
for z in zombies:
    print(z)

# Let's check SQLite as well for these specific scans
import sqlite3
sqlite_conn = sqlite3.connect('cache/screener.db')
sqlite_cur = sqlite_conn.cursor()

print("\nSQLITE DATA FOR ZOMBIES:")
for z in zombies:
    scan_id = z[0]
    sqlite_cur.execute("SELECT status FROM scan_runs WHERE scan_id = ?", (scan_id,))
    res = sqlite_cur.fetchone()
    print(f"{scan_id} scan_runs in SQLite: {res}")
    
    sqlite_cur.execute("SELECT old_state, new_state FROM scan_state_transitions WHERE scan_id = ?", (scan_id,))
    res = sqlite_cur.fetchall()
    print(f"{scan_id} transitions in SQLite: {res}")

    sqlite_cur.execute("SELECT event_type FROM scan_event_audit WHERE scan_id = ?", (scan_id,))
    res = sqlite_cur.fetchall()
    print(f"{scan_id} events in SQLite: {res}")
    
conn.close()
