import psycopg2
import json

DATABASE_URL = "postgresql://postgres.tithybqsriohuzpatmfa:AISCANtomar1@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"

def run_query(cur, query):
    try:
        cur.execute(query)
        if query.strip().upper().startswith("SELECT"):
            try:
                results = cur.fetchall()
                if not results:
                    return []
                # Check if it's a single count
                if len(results) == 1 and len(results[0]) == 1:
                    return results[0][0]
                return [list(r) for r in results]
            except Exception as e:
                return str(e)
        return "Executed"
    except Exception as e:
        return f"ERROR: {str(e)}"

evidence = {}

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Phase B
        evidence["catalog_total_rows"] = run_query(cur, "SELECT COUNT(*) AS total_rows FROM universe_catalog;")
        evidence["catalog_active_rows"] = run_query(cur, "SELECT COUNT(*) AS active_rows FROM universe_catalog WHERE is_active = TRUE;")
        evidence["catalog_distinct_symbols"] = run_query(cur, "SELECT COUNT(DISTINCT symbol) FROM universe_catalog;")
        evidence["catalog_buckets"] = run_query(cur, "SELECT market_cap_bucket, COUNT(*) FROM universe_catalog GROUP BY market_cap_bucket ORDER BY market_cap_bucket;")
        
        # Phase F
        evidence["chunk_runs_sample"] = run_query(cur, "SELECT id, scan_id, chunk_name, status, symbols_processed FROM universe_chunk_runs ORDER BY started_at DESC LIMIT 20;")
        
        # Phase G
        evidence["snapshots_count"] = run_query(cur, "SELECT COUNT(*) FROM research_snapshots_v2;")
        evidence["advisories_count"] = run_query(cur, "SELECT COUNT(*) FROM research_advisories;")

    conn.close()
    evidence["connection_success"] = True
except Exception as e:
    evidence["connection_success"] = False
    evidence["connection_error"] = str(e)

print(json.dumps(evidence, indent=2))
