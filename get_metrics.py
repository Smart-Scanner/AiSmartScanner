import sqlite3
import pandas as pd
import json

conn = sqlite3.connect('cache/screener.db')

try:
    df = pd.read_sql("SELECT data FROM scan_results", conn)
    
    hcs = []
    goldens = []
    for d_str in df['data']:
        try:
            d = json.loads(d_str)
            if d.get('high_conviction'):
                hcs.append(d)
            if d.get('is_golden'):
                goldens.append(d)
        except: pass
        
    print(f"HC Count: {len(hcs)}")
    for d in hcs:
        print(f"- {d.get('symbol')}: Score {d.get('score')} | RSI {d.get('rsi')} | Vol {d.get('volume_ratio')}")
        
    print(f"\nGolden Count: {len(goldens)}")
except Exception as e:
    print("Error:", e)
