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

scan_id = 'scan_auto_1781171295_d2b50e'

cur.execute("SELECT * FROM scan_runs WHERE scan_id = %s", (scan_id,))
print("Scan run:", cur.fetchone())

conn.close()
