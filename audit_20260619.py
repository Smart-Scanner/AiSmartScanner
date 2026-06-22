import sqlite3

c = sqlite3.connect('cache/screener.db')
c.row_factory = sqlite3.Row

res = dict(c.execute("SELECT COUNT(*) as cnt, SUM(CASE WHEN high_conviction = 1 THEN 1 ELSE 0 END) as hc_cnt, SUM(CASE WHEN score > 0 THEN 1 ELSE 0 END) as scored_cnt FROM scan_results WHERE scan_date = '2026-06-19'").fetchone())
print("scan_results for 2026-06-19:", res)

res_final = dict(c.execute("SELECT COUNT(*) as cnt, SUM(CASE WHEN high_conviction = 1 THEN 1 ELSE 0 END) as hc_cnt, SUM(CASE WHEN final_score > 0 THEN 1 ELSE 0 END) as scored_cnt FROM final_scores WHERE scan_date = '2026-06-19'").fetchone())
print("final_scores for 2026-06-19:", res_final)

try:
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recommendation_snapshots'")
    if c.fetchone():
        # recommendation snapshots don't have scan_date, but they have created_at
        res_rec = dict(c.execute("SELECT COUNT(*) as cnt FROM recommendation_snapshots WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("recommendation_snapshots for 2026-06-19:", res_rec)
except Exception as e:
    print("rec error", e)
    
try:
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_snapshots_v2'")
    if c.fetchone():
        res_rs = dict(c.execute("SELECT COUNT(*) as cnt FROM research_snapshots_v2 WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("research_snapshots_v2 for 2026-06-19:", res_rs)
except Exception as e:
    print("rs error", e)

try:
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_orders'")
    if c.fetchone():
        res_po = dict(c.execute("SELECT COUNT(*) as cnt FROM paper_orders WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("paper_orders for 2026-06-19:", res_po)
except Exception as e:
    print("po error", e)
