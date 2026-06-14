import db

def print_schema(table_name):
    print(f'-- Schema for {table_name} --')
    try:
        rows = db.execute_db(f'PRAGMA table_info({table_name})', fetch='all')
        for r in rows:
            print(f"{r.get('name')} {r.get('type')}")
    except Exception as e:
        print('Error:', e)

print_schema('research_snapshots_v2')
print_schema('scan_results')
