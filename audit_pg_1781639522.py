import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url, connect_timeout=10)
cur = conn.cursor()

scan_id = 'scan_manual_1781639522_10e8e4'

cur.execute("SELECT old_state, new_state, reason, created_at FROM scan_state_transitions WHERE scan_id = %s", (scan_id,))
transitions = cur.fetchall()
print("Transitions:", transitions)

cur.execute("SELECT event_type, details, created_at FROM scan_event_audit WHERE scan_id = %s", (scan_id,))
events = cur.fetchall()
print("Events:")
for e in events:
    print(e)
    
cur.execute("SELECT error_message FROM scan_runs WHERE scan_id = %s", (scan_id,))
print("Error Message:", cur.fetchone())

conn.close()
