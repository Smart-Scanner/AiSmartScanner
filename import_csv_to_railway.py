"""
CSV to Railway PostgreSQL — Paper Trades Importer
===================================================
Apna CSV file ka naam neeche rakho aur script chalao.

Usage:
    python import_csv_to_railway.py paper_trades_backup.csv
"""

import sys
import os
import csv
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Railway DB URL — .env se lega
# DB URL: second argument se override kar sakte ho
# Usage: python import_csv_to_railway.py file.csv "postgresql://..."
RAILWAY_URL = sys.argv[2] if len(sys.argv) > 2 else os.getenv("DATABASE_URL", "")

if not RAILWAY_URL:
    print("ERROR: DATABASE_URL nahi mila!")
    print("Option 1: .env mein DATABASE_URL set karo")
    print("Option 2: python import_csv_to_railway.py file.csv \"postgresql://...\"")
    sys.exit(1)

# CSV file
CSV_FILE = sys.argv[1] if len(sys.argv) > 1 else "paper_trades_backup.csv"

if not os.path.exists(CSV_FILE):
    print(f"ERROR: CSV file nahi mili: {CSV_FILE}")
    print("Usage: python import_csv_to_railway.py <filename.csv>")
    sys.exit(1)

print(f"CSV file: {CSV_FILE}")
print(f"Railway DB: {RAILWAY_URL[:50]}...")
print()

try:
    conn = psycopg2.connect(RAILWAY_URL, cursor_factory=RealDictCursor, connect_timeout=10)
    conn.autocommit = False
    cur = conn.cursor()
    print("[OK] Railway DB se connected!")
except Exception as e:
    print(f"[FAIL] DB connection failed: {e}")
    sys.exit(1)

# CSV read karo
with open(CSV_FILE, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

if not rows:
    print("CSV mein koi data nahi!")
    sys.exit(0)

print(f"CSV mein {len(rows)} rows hain")
print(f"Columns: {list(rows[0].keys())}")
print()

# DB ke paper_trades columns check karo
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'paper_trades' 
    ORDER BY ordinal_position
""")
db_cols = {r['column_name']: r['data_type'] for r in cur.fetchall()}
print(f"DB mein paper_trades columns: {len(db_cols)}")

# CSV columns jo DB mein hain — match karo
csv_cols = list(rows[0].keys())
valid_cols = [c for c in csv_cols if c in db_cols and c != 'id']  # id skip (auto-increment)
skipped_cols = [c for c in csv_cols if c not in db_cols]

if skipped_cols:
    print(f"⚠️  Ye CSV columns DB mein nahi hain (skip honge): {skipped_cols}")

print(f"✅ Import honge ye {len(valid_cols)} columns: {valid_cols}")
print()

# Insert karo
inserted = 0
skipped = 0
errors = 0

def clean_val(val, col_name, data_type):
    """NULL aur type conversions handle karo"""
    if val == '' or val is None or val == 'None' or val == 'NULL':
        return None
    if data_type in ('integer', 'bigint'):
        try: return int(float(val))
        except: return None
    if data_type in ('real', 'double precision', 'numeric'):
        try: return float(val)
        except: return None
    if data_type == 'boolean':
        return val.lower() in ('true', '1', 'yes', 't')
    return val

cols_str = ', '.join(f'"{c}"' for c in valid_cols)
placeholders = ', '.join(['%s'] * len(valid_cols))
insert_sql = f'INSERT INTO paper_trades ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

for i, row in enumerate(rows):
    try:
        values = [clean_val(row.get(c, ''), c, db_cols.get(c, 'text')) for c in valid_cols]
        cur.execute(insert_sql, values)
        inserted += 1
        if (i+1) % 50 == 0:
            print(f"  {i+1}/{len(rows)} rows processed...")
    except Exception as e:
        errors += 1
        print(f"  [ERR] Row {i+1} error: {e} | Data: {dict(list(row.items())[:3])}")
        conn.rollback()
        # Reconnect after error
        conn.autocommit = False

try:
    conn.commit()
    print()
    print("=" * 50)
    print("[DONE] IMPORT COMPLETE!")
    print(f"   Inserted: {inserted}")
    print(f"   Errors:   {errors}")
    print("=" * 50)
except Exception as e:
    print(f"[FAIL] Commit failed: {e}")
    conn.rollback()

conn.close()
