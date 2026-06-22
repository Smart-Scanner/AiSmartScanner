import re

with open("db.py", "r", encoding="utf-8") as f:
    content = f.read()

# For log_slim_coverage
content = content.replace(
    'execute_db("SELECT COUNT(*) as total, COUNT(slim_data) as slim FROM scan_results"',
    'execute_db("SELECT COUNT(*) as total, COUNT(slim_data) as slim FROM scan_results_v2 WHERE scan_id = ?", (get_latest_completed_scan_id(),)'
)

# For load_results
content = content.replace(
    '"SELECT slim_data FROM scan_results WHERE slim_data IS NOT NULL ORDER BY score DESC LIMIT ?",\n                (limit,)',
    '"SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?",\n                (get_latest_completed_scan_id(), limit,)'
)
content = content.replace(
    '"SELECT data FROM scan_results WHERE slim_data IS NULL ORDER BY score DESC LIMIT ?",\n                    (remaining,)',
    '"SELECT data FROM scan_results_v2 WHERE scan_id = ? AND slim_data IS NULL ORDER BY score DESC LIMIT ?",\n                    (get_latest_completed_scan_id(), remaining,)'
)
content = content.replace(
    '"SELECT data FROM scan_results ORDER BY score DESC LIMIT ?",\n                (limit,)',
    '"SELECT data FROM scan_results_v2 WHERE scan_id = ? ORDER BY score DESC LIMIT ?",\n                (get_latest_completed_scan_id(), limit,)'
)

# For get_golden_results
content = content.replace(
    'pg_query = "SELECT slim_data FROM scan_results WHERE ((slim_data->>\'is_golden\')::text = \'true\' OR (slim_data->>\'is_golden\')::text = \'1\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"',
    'pg_query = "SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND ((slim_data->>\'is_golden\')::text = \'true\' OR (slim_data->>\'is_golden\')::text = \'1\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'sqlite_query = "SELECT slim_data FROM scan_results WHERE (json_extract(slim_data, \'$.is_golden\') = 1 OR json_extract(slim_data, \'$.is_golden\') = \'true\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"',
    'sqlite_query = "SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(slim_data, \'$.is_golden\') = 1 OR json_extract(slim_data, \'$.is_golden\') = \'true\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'pg_query = "SELECT data FROM scan_results WHERE (data->>\'is_golden\')::text = \'true\' OR (data->>\'is_golden\')::text = \'1\' ORDER BY score DESC LIMIT ?"',
    'pg_query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND ((data->>\'is_golden\')::text = \'true\' OR (data->>\'is_golden\')::text = \'1\') ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'sqlite_query = "SELECT data FROM scan_results WHERE json_extract(data, \'$.is_golden\') = 1 OR json_extract(data, \'$.is_golden\') = \'true\' ORDER BY score DESC LIMIT ?"',
    'sqlite_query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(data, \'$.is_golden\') = 1 OR json_extract(data, \'$.is_golden\') = \'true\') ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'execute_db(query, (limit,), fetch="all")',
    'execute_db(query, (get_latest_completed_scan_id(), limit), fetch="all")'
)

# Fix fallback queries inside get_golden_results
content = content.replace(
    'fallback_pg = "SELECT data FROM scan_results WHERE ((data->>\'is_golden\')::text = \'true\' OR (data->>\'is_golden\')::text = \'1\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"',
    'fallback_pg = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND ((data->>\'is_golden\')::text = \'true\' OR (data->>\'is_golden\')::text = \'1\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'fallback_sqlite = "SELECT data FROM scan_results WHERE (json_extract(data, \'$.is_golden\') = 1 OR json_extract(data, \'$.is_golden\') = \'true\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"',
    'fallback_sqlite = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(data, \'$.is_golden\') = 1 OR json_extract(data, \'$.is_golden\') = \'true\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'f_rows = execute_db(fallback_query, (remaining,), fetch="all")',
    'f_rows = execute_db(fallback_query, (get_latest_completed_scan_id(), remaining), fetch="all")'
)

# For get_breakout_results
content = content.replace(
    'pg_query = "SELECT slim_data FROM scan_results WHERE ((slim_data->>\'is_breakout\')::text = \'true\' OR (slim_data->>\'is_breakout\')::text = \'1\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"',
    'pg_query = "SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND ((slim_data->>\'is_breakout\')::text = \'true\' OR (slim_data->>\'is_breakout\')::text = \'1\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'sqlite_query = "SELECT slim_data FROM scan_results WHERE (json_extract(slim_data, \'$.is_breakout\') = 1 OR json_extract(slim_data, \'$.is_breakout\') = \'true\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"',
    'sqlite_query = "SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(slim_data, \'$.is_breakout\') = 1 OR json_extract(slim_data, \'$.is_breakout\') = \'true\') AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'pg_query = "SELECT data FROM scan_results WHERE (data->>\'is_breakout\')::text = \'true\' OR (data->>\'is_breakout\')::text = \'1\' ORDER BY score DESC LIMIT ?"',
    'pg_query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND ((data->>\'is_breakout\')::text = \'true\' OR (data->>\'is_breakout\')::text = \'1\') ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'sqlite_query = "SELECT data FROM scan_results WHERE json_extract(data, \'$.is_breakout\') = 1 OR json_extract(data, \'$.is_breakout\') = \'true\' ORDER BY score DESC LIMIT ?"',
    'sqlite_query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(data, \'$.is_breakout\') = 1 OR json_extract(data, \'$.is_breakout\') = \'true\') ORDER BY score DESC LIMIT ?"'
)

# Fix fallback inside get_breakout_results
content = content.replace(
    'fallback_pg = "SELECT data FROM scan_results WHERE ((data->>\'is_breakout\')::text = \'true\' OR (data->>\'is_breakout\')::text = \'1\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"',
    'fallback_pg = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND ((data->>\'is_breakout\')::text = \'true\' OR (data->>\'is_breakout\')::text = \'1\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'fallback_sqlite = "SELECT data FROM scan_results WHERE (json_extract(data, \'$.is_breakout\') = 1 OR json_extract(data, \'$.is_breakout\') = \'true\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"',
    'fallback_sqlite = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND (json_extract(data, \'$.is_breakout\') = 1 OR json_extract(data, \'$.is_breakout\') = \'true\') AND slim_data IS NULL ORDER BY score DESC LIMIT ?"'
)

# For get_high_conviction_results
content = content.replace(
    'query = "SELECT slim_data FROM scan_results WHERE high_conviction = 1 AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"',
    'query = "SELECT slim_data FROM scan_results_v2 WHERE scan_id = ? AND high_conviction = 1 AND slim_data IS NOT NULL ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'query = "SELECT data FROM scan_results WHERE high_conviction = 1 ORDER BY score DESC LIMIT ?"',
    'query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND high_conviction = 1 ORDER BY score DESC LIMIT ?"'
)
content = content.replace(
    'fallback_query = "SELECT data FROM scan_results WHERE high_conviction = 1 AND slim_data IS NULL ORDER BY score DESC LIMIT ?"',
    'fallback_query = "SELECT data FROM scan_results_v2 WHERE scan_id = ? AND high_conviction = 1 AND slim_data IS NULL ORDER BY score DESC LIMIT ?"'
)

with open("db.py", "w", encoding="utf-8") as f:
    f.write(content)
