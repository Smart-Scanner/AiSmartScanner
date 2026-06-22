import sqlite3
c = sqlite3.connect('cache/screener.db')
cur = c.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(tables)
c.close()
