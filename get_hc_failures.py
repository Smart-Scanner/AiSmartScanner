import sqlite3
import json

conn = sqlite3.connect('cache/screener.db')
c = conn.cursor()
c.execute("SELECT data FROM scan_results")
rows = c.fetchall()

for (data_str,) in rows:
    try:
        d = json.loads(data_str)
        if d.get('score', 0) >= 55:
            print(f"--- {d.get('symbol')} ---")
            print(f"Score: {d.get('score')}")
            print(f"RSI (40-70): {d.get('rsi')}")
            print(f"Delivery (>40): {d.get('delivery_pct')}")
            print(f"Risk (<40): {d.get('risk_score')}")
            print(f"Volume Ratio (>1.0): {d.get('volume_ratio')}")
            print(f"Risk/Reward (>2.2): {d.get('risk_reward')}")
            signals = d.get('signals', [])
            bullish = sum(1 for s in signals if s[2] == 'bullish')
            print(f"Bullish Signals (>4): {bullish}")
    except: pass
