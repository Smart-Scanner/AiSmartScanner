import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()

def check_schema(table_name):
    print(f'-- Postgres Schema for {table_name} --')
    cur.execute('SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s', (table_name,))
    rows = cur.fetchall()
    for r in rows:
        print(f'{r[0]} {r[1]}')

check_schema('research_snapshots_v2')
check_schema('scan_results')
