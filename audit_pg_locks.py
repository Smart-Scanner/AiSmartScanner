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

cur.execute("SELECT scan_id, owner_id FROM scan_lock WHERE scan_id IS NOT NULL")
locks = cur.fetchall()
print('PG Locks:', locks)

cur.execute("SELECT scan_id, status FROM scan_resume_state")
resumes = cur.fetchall()
print('PG Resumes:', resumes)

cur.execute("SELECT id, scan_id, status FROM current_scan_state")
currents = cur.fetchall()
print('PG Current Scan State:', currents)

conn.close()
