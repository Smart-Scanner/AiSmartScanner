import psycopg2
from psycopg2.extras import RealDictCursor

SUPABASE_URL = "postgresql://postgres.yrrxwhiivdbmcqhiinsj:Quant00DB1314@aws-1-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require"

try:
    print("Supabase se connect ho raha hai...")
    conn = psycopg2.connect(SUPABASE_URL, cursor_factory=RealDictCursor, connect_timeout=10)
    cur = conn.cursor()
    
    # Check scan_meta for fyers token
    cur.execute("SELECT key, value FROM scan_meta WHERE key ILIKE '%fyers%' OR key ILIKE '%token%'")
    rows = cur.fetchall()
    
    if rows:
        print("\n=== FYERS TOKEN MILA ===")
        for r in rows:
            print(f"Key: {r['key']}")
            print(f"Value: {r['value']}")
    else:
        print("scan_meta mein fyers token nahi mila")
        # Check all scan_meta keys
        cur.execute("SELECT key FROM scan_meta ORDER BY key")
        all_keys = cur.fetchall()
        print("\nSaari keys jo hain scan_meta mein:")
        for k in all_keys:
            print(" -", k['key'])
    
    conn.close()
except Exception as e:
    print("Error:", e)
