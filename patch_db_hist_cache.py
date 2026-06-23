import re

def patch_db():
    with open("db.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add Postgres CREATE TABLE
    pg_create = """                        CREATE TABLE IF NOT EXISTS paper_orders (
                            id SERIAL PRIMARY KEY,
                            -- ... (existing paper_orders)
                        );
"""
    # Let's just find "CREATE TABLE IF NOT EXISTS paper_orders" and inject before it
    pg_table = """
                        CREATE TABLE IF NOT EXISTS historical_cache (
                            symbol_token TEXT NOT NULL,
                            exchange TEXT NOT NULL,
                            timeframe TEXT NOT NULL,
                            last_refresh TIMESTAMP NOT NULL,
                            expires_at TIMESTAMP NOT NULL,
                            payload_json JSONB NOT NULL,
                            PRIMARY KEY(symbol_token, exchange, timeframe)
                        );
                        
"""
    content = content.replace("                        CREATE TABLE IF NOT EXISTS paper_orders (", 
                              pg_table + "                        CREATE TABLE IF NOT EXISTS paper_orders (")

    # 2. Add SQLite CREATE TABLE
    sqlite_table = """
            CREATE TABLE IF NOT EXISTS historical_cache (
                symbol_token TEXT NOT NULL,
                exchange TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                last_refresh TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                payload_json JSON NOT NULL,
                PRIMARY KEY(symbol_token, exchange, timeframe)
            );
            
"""
    content = content.replace("            CREATE TABLE IF NOT EXISTS paper_orders (", 
                              sqlite_table + "            CREATE TABLE IF NOT EXISTS paper_orders (")

    # 3. Append helper functions
    helpers = """

# --- Historical Cache API ---
def get_historical_cache(symbol_token: str, exchange: str, timeframe: str, allow_stale: bool = False):
    try:
        if PG_ENABLED:
            with get_pg_connection() as conn:
                with conn.cursor() as cursor:
                    if allow_stale:
                        cursor.execute("SELECT payload_json FROM historical_cache WHERE symbol_token = %s AND exchange = %s AND timeframe = %s", (symbol_token, exchange, timeframe))
                    else:
                        cursor.execute("SELECT payload_json FROM historical_cache WHERE symbol_token = %s AND exchange = %s AND timeframe = %s AND expires_at > NOW()", (symbol_token, exchange, timeframe))
                    row = cursor.fetchone()
                    if row:
                        return row[0]
                    return None
        else:
            with _get_connection() as conn:
                if allow_stale:
                    cursor = conn.execute("SELECT payload_json FROM historical_cache WHERE symbol_token = ? AND exchange = ? AND timeframe = ?", (symbol_token, exchange, timeframe))
                else:
                    cursor = conn.execute("SELECT payload_json FROM historical_cache WHERE symbol_token = ? AND exchange = ? AND timeframe = ? AND expires_at > datetime('now')", (symbol_token, exchange, timeframe))
                row = cursor.fetchone()
                if row:
                    import json
                    return json.loads(row[0])
                return None
    except Exception as e:
        log.error("Failed to get historical_cache for %s: %s", symbol_token, e)
        return None

def set_historical_cache(symbol_token: str, exchange: str, timeframe: str, payload: list, ttl_hours: int = 24):
    import json
    try:
        payload_str = json.dumps(payload)
        if PG_ENABLED:
            with get_pg_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO historical_cache (symbol_token, exchange, timeframe, last_refresh, expires_at, payload_json)
                        VALUES (%s, %s, %s, NOW(), NOW() + interval '%s hours', %s)
                        ON CONFLICT (symbol_token, exchange, timeframe)
                        DO UPDATE SET last_refresh = EXCLUDED.last_refresh, expires_at = EXCLUDED.expires_at, payload_json = EXCLUDED.payload_json
                    ''', (symbol_token, exchange, timeframe, ttl_hours, payload_str))
                conn.commit()
        else:
            with _get_connection() as conn:
                conn.execute('''
                    INSERT INTO historical_cache (symbol_token, exchange, timeframe, last_refresh, expires_at, payload_json)
                    VALUES (?, ?, ?, datetime('now'), datetime('now', '+' || ? || ' hours'), ?)
                    ON CONFLICT(symbol_token, exchange, timeframe) DO UPDATE SET
                    last_refresh=excluded.last_refresh, expires_at=excluded.expires_at, payload_json=excluded.payload_json
                ''', (symbol_token, exchange, timeframe, ttl_hours, payload_str))
                conn.commit()
    except Exception as e:
        log.error("Failed to set historical_cache for %s: %s", symbol_token, e)
"""
    if "def get_historical_cache" not in content:
        content += helpers

    with open("db.py", "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched db.py successfully.")

if __name__ == "__main__":
    patch_db()
