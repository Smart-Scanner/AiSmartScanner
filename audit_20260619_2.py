import sqlite3

c = sqlite3.connect('cache/screener.db')
c.row_factory = sqlite3.Row

try:
    if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recommendation_snapshots'").fetchone():
        res_rec = dict(c.execute("SELECT COUNT(*) as cnt FROM recommendation_snapshots WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("recommendation_snapshots for 2026-06-19:", res_rec)
except Exception as e:
    print("rec error", e)
    
try:
    if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_snapshots_v2'").fetchone():
        res_rs = dict(c.execute("SELECT COUNT(*) as cnt FROM research_snapshots_v2 WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("research_snapshots_v2 for 2026-06-19:", res_rs)
except Exception as e:
    print("rs error", e)

try:
    if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_orders'").fetchone():
        res_po = dict(c.execute("SELECT COUNT(*) as cnt FROM paper_orders WHERE created_at LIKE '2026-06-19%'").fetchone())
        print("paper_orders for 2026-06-19:", res_po)
except Exception as e:
    print("po error", e)
