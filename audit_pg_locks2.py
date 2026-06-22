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

cur.execute("SELECT status FROM scan_runs WHERE scan_id = 'scan_manual_1781639522_10e8e4'")
print("scan_runs status:", cur.fetchone())

conn.close()
