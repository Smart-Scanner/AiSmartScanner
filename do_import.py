import os, csv, psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv()

URL = os.getenv('DATABASE_URL')
CSV_FILE = r'C:\Users\91971\Downloads\paper_trades_rows.csv'

conn = psycopg2.connect(URL, cursor_factory=RealDictCursor, connect_timeout=10)
conn.autocommit = False
cur = conn.cursor()
print('Connected to Railway DB!')

with open(CSV_FILE, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print('CSV rows: %d' % len(rows))

cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='paper_trades' ORDER BY ordinal_position")
db_cols = {r['column_name']: r['data_type'] for r in cur.fetchall()}
print('DB columns: %d' % len(db_cols))

csv_cols = list(rows[0].keys())
valid_cols = [c for c in csv_cols if c in db_cols and c != 'id']
skipped = [c for c in csv_cols if c not in db_cols]
if skipped:
    print('Skipped columns (not in DB): %s' % skipped)
print('Importing %d columns...' % len(valid_cols))

def clean(val, dt):
    if val in ('', None, 'None', 'NULL'):
        return None
    if dt in ('integer', 'bigint'):
        try:
            return int(float(val))
        except:
            return None
    if dt in ('real', 'double precision', 'numeric'):
        try:
            return float(val)
        except:
            return None
    if dt == 'boolean':
        return val.lower() in ('true', '1', 'yes')
    return val

cols_sql = ', '.join('"' + c + '"' for c in valid_cols)
ph = ', '.join(['%s'] * len(valid_cols))
sql = 'INSERT INTO paper_trades (' + cols_sql + ') VALUES (' + ph + ') ON CONFLICT DO NOTHING'

inserted = 0
errors = 0
for i, row in enumerate(rows):
    try:
        vals = [clean(row.get(c, ''), db_cols.get(c, 'text')) for c in valid_cols]
        cur.execute(sql, vals)
        inserted += 1
    except Exception as e:
        errors += 1
        print('Row %d error: %s' % (i+1, str(e)[:100]))

conn.commit()
conn.close()
print('')
print('========================================')
print('IMPORT COMPLETE!')
print('Inserted: %d' % inserted)
print('Errors:   %d' % errors)
print('========================================')
