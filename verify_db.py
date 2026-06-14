import os
from urllib.parse import urlparse
from dotenv import load_dotenv

import db

def main():
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL Source: MISSING")
        return
        
    parsed = urlparse(db_url)
    host = parsed.hostname
    dbname = parsed.path.lstrip('/')
    user = parsed.username
    
    print(f"DATABASE_URL Source: .env")
    print(f"Active DB Host: {host}")
    print(f"Active DB Name: {dbname}")
    print(f"Active DB User: {user}")
    
    is_pg = db.is_postgresql()
    print(f"PostgreSQL Connected: {is_pg}")
    print(f"SQLite Fallback Active: {not is_pg}")
    print(f"Railway Environment: {'RAILWAY_ENVIRONMENT' in os.environ}")

if __name__ == "__main__":
    main()
