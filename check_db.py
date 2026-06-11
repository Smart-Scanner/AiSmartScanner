import os
import json
import psycopg2

def main():
    from dotenv import load_dotenv
    load_dotenv()
    
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("No DATABASE_URL found.")
        return

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Get tables
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        tables = [r[0] for r in cur.fetchall()]
        
        schema_info = {}
        for table in tables:
            cur.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
            """, (table,))
            columns = {r[0]: {"type": r[1], "nullable": r[2], "default": r[3]} for r in cur.fetchall()}
            schema_info[table] = columns
            
        with open("prod_schema.json", "w") as f:
            json.dump(schema_info, f, indent=2)
            
        print("Schema successfully written to prod_schema.json")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
