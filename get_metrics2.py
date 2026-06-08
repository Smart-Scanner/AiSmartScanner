import sqlite3
import json

conn = sqlite3.connect('cache/screener.db')
c = conn.cursor()
c.execute("SELECT data FROM scan_results")
rows = c.fetchall()

scores = []
universe = len(rows)

for (data_str,) in rows:
    try:
        d = json.loads(data_str)
        scores.append(d.get('score', 0))
    except: pass

scores.sort()

def pct(p):
    if not scores: return 0
    return scores[int(len(scores) * p / 100)]

print(f"Total Scanned: {universe}")
if scores:
    print(f"Max Score: {scores[-1]}")
    print(f"Avg Score: {sum(scores)/len(scores):.1f}")
    print(f"P50 Score: {pct(50)}")
    print(f"P75 Score: {pct(75)}")
    print(f"P90 Score: {pct(90)}")
    print(f"P95 Score: {pct(95)}")
    print(f"P99 Score: {pct(99)}")
    print(f"Scores > 55: {sum(1 for s in scores if s >= 55)}")
    print(f"Scores > 50: {sum(1 for s in scores if s >= 50)}")
