from dotenv import load_dotenv
load_dotenv()
import db

queries = [
    ("Total fundamentals rows", "SELECT COUNT(*) as c FROM fundamentals"),
    ("PE not null", "SELECT COUNT(*) as c FROM fundamentals WHERE pe IS NOT NULL"),
    ("Market Cap not null", "SELECT COUNT(*) as c FROM fundamentals WHERE market_cap IS NOT NULL"),
    ("ROE not null", "SELECT COUNT(*) as c FROM fundamentals WHERE roe IS NOT NULL"),
    ("Scan results count", "SELECT COUNT(*) as c FROM scan_results"),
    ("Max score", "SELECT MAX(score) as c FROM scan_results"),
    ("Score >= 65", "SELECT symbol, score FROM scan_results WHERE score >= 65 ORDER BY score DESC"),
    ("Snapshots count", "SELECT COUNT(*) as c FROM research_snapshots_v2"),
    ("Snapshot details", "SELECT symbol, version, status, score_at_generation, cmp_at_generation, created_at FROM research_snapshots_v2 ORDER BY created_at DESC LIMIT 20"),
    ("Distinct fundamentals symbols", "SELECT COUNT(DISTINCT symbol) as c FROM fundamentals"),
    ("Distinct stocks symbols", "SELECT COUNT(DISTINCT symbol) as c FROM stocks"),
]

for label, q in queries:
    try:
        if "symbol, score" in q or "symbol, version" in q:
            rows = db.execute_db(q, fetch="all")
            print(f"\n--- {label} ---")
            if rows:
                for r in rows:
                    print(dict(r))
            else:
                print("0 rows")
        else:
            row = db.execute_db(q, fetch="one")
            print(f"{label}: {row.get('c') if row else 'N/A'}")
    except Exception as e:
        print(f"{label}: ERROR - {e}")
