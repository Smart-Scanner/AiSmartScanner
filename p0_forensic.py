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
        # P0-3
        evidence["catalog_count"] = run_query(cur, "SELECT COUNT(*) FROM universe_catalog;")
        evidence["snapshots_count"] = run_query(cur, "SELECT COUNT(*) FROM research_snapshots_v2;")
        evidence["advisories_count"] = run_query(cur, "SELECT COUNT(*) FROM research_advisories;")
        
        # Wrap chunk_runs query in try to avoid aborting transaction
        try:
            evidence["chunk_runs_count"] = run_query(cur, "SELECT COUNT(*) FROM universe_chunk_runs;")
        except Exception:
            evidence["chunk_runs_count"] = "ERROR or Table Missing"
            conn.rollback()
            
        evidence["scan_runs_count"] = run_query(cur, "SELECT COUNT(*) FROM scan_runs;")
        
        # P0-4 Trace Scanner Universe Source
        latest_scan = run_query(cur, "SELECT id, total_symbols FROM scan_runs ORDER BY start_time DESC LIMIT 1;")
        evidence["latest_scan"] = latest_scan

        if latest_scan and isinstance(latest_scan, list) and len(latest_scan) > 0:
            scan_id = latest_scan[0][0]
            evidence["symbols_in_latest_scan"] = run_query(cur, f"SELECT COUNT(DISTINCT symbol) FROM scan_state_transitions WHERE scan_id = '{scan_id}';")
        else:
            evidence["symbols_in_latest_scan"] = 0

    conn.close()
    evidence["connection_success"] = True
except Exception as e:
    evidence["connection_success"] = False
    evidence["connection_error"] = str(e)

print(json.dumps(evidence, indent=2))
