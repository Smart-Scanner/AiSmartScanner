import os
import traceback
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.getenv("DATABASE_URL")

print("DATABASE_URL PRESENT:", bool(db_url))

try:
    conn = psycopg2.connect(
        db_url,
        connect_timeout=15
    )
    
    print("POSTGRES CONNECTION SUCCESS")
    
    cur = conn.cursor()
    
    cur.execute("select version()")
    print(cur.fetchone())
    
    cur.execute("""
        select current_database(),
               current_user
    """)
    
    print(cur.fetchone())
    
    conn.close()

except Exception as exc:
    print("POSTGRES CONNECTION FAILED")
    print(type(exc).__name__)
    print(str(exc))
    traceback.print_exc()
