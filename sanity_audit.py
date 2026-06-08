import sqlite3
import json
from pathlib import Path

# Connect to DB
db_path = Path("cache/screener.db")
if not db_path.exists():
    print("DB NOT FOUND")
    exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT data FROM scan_results")
rows = c.fetchall()

results = []
for (data_str,) in rows:
    try:
        results.append(json.loads(data_str))
    except:
        pass

results.sort(key=lambda x: x.get("score", 0), reverse=True)

# Data Quality (Audit E)
total_analyzed = len(results)
fundamental_coverage = sum(1 for r in results if r.get("fundamental_score", 0) > 0)
earnings_coverage = sum(1 for r in results if r.get("earnings_momentum_score", 0) > 0)
print("=== AUDIT E: DATA QUALITY ===")
print(f"Stocks Analyzed: {total_analyzed}")
print(f"Fundamental Coverage: {fundamental_coverage} ({(fundamental_coverage/total_analyzed)*100:.1f}%)")
print(f"Earnings Coverage: {earnings_coverage} ({(earnings_coverage/total_analyzed)*100:.1f}%)")
print()

# Top 20 (Audit A & C)
top_20 = results[:20]
print("=== AUDIT A & C: TOP 20 ===")
components = {
    "Technical": [], "Earnings Momentum": [], "Fundamental": [],
    "Smart Money": [], "Sector Rotation": [], "News Sentiment": [],
    "News Spike": [], "Macro": [], "Catalyst": []
}

for i, r in enumerate(top_20):
    sym = r.get("symbol")
    score = r.get("score", 0)
    hc = r.get("high_conviction", False)
    golden = r.get("is_golden", False)
    risk = r.get("risk_score", 0)
    rr = r.get("risk_reward", 0)
    vol = r.get("volume_ratio", 0)
    deliv = r.get("delivery_pct", 0)
    atr = r.get("atr_pct", 0)
    rsi = r.get("rsi", 0)
    sector = r.get("sector", "Unknown")
    sector_rot = r.get("sector_rotation_score", 0)
    
    print(f"{i+1}. {sym} | Score: {score} | HC: {hc} | Golden: {golden} | Risk: {risk} | RR: {rr} | Vol: {vol} | Deliv: {deliv} | ATR: {atr} | RSI: {rsi:.1f} | Sector: {sector} | SectorRot: {sector_rot}")
    
    components["Technical"].append(r.get("technical_score", 0))
    components["Earnings Momentum"].append(r.get("earnings_momentum_score", 0))
    components["Fundamental"].append(r.get("fundamental_score", 0))
    components["Smart Money"].append(r.get("smart_money_score", 0))
    components["Sector Rotation"].append(sector_rot)
    components["News Sentiment"].append(r.get("news_sentiment_score", 0))
    components["News Spike"].append(r.get("news_spike_score", 0))
    components["Macro"].append(r.get("macro_score", 0))
    components["Catalyst"].append(r.get("marketaux_catalyst_score", 0))

print("\n=== AUDIT C: AVG COMPONENTS (Top 20) ===")
for k, v in components.items():
    avg = sum(v) / len(v) if v else 0
    print(f"{k}: {avg:.1f}")
print()

# Bottom 10 (Audit D)
bottom_10 = results[-10:]
print("=== AUDIT D: BOTTOM 10 ===")
for i, r in enumerate(bottom_10):
    sym = r.get("symbol")
    score = r.get("score", 0)
    risk = r.get("risk_score", 0)
    tech = r.get("technical_score", 0)
    fund = r.get("fundamental_score", 0)
    print(f"{len(results)-9+i}. {sym} | Score: {score} | Tech: {tech} | Fund: {fund} | Risk: {risk}")
print()

# HC Rejection (Audit B)
# Score >= 55, RSI in 40-70, Deliv >= 40, ATR in 1.5-5.5, Risk <= 40, RR >= 2.2, Vol >= 1.0
print("=== AUDIT B: HC REJECTION (Top 20) ===")
for r in top_20:
    sym = r.get("symbol")
    score = r.get("score", 0)
    hc = r.get("high_conviction", False)
    if hc: continue
    
    failures = []
    if score < 55: failures.append(f"Score {score} < 55")
    rsi = r.get("rsi", 0)
    if not (40 <= rsi <= 70): failures.append(f"RSI {rsi:.1f} not in 40-70")
    deliv = r.get("delivery_pct", 0)
    if deliv < 40: failures.append(f"Deliv {deliv} < 40")
    atr = r.get("atr_pct", 0)
    if not (1.5 <= atr <= 5.5): failures.append(f"ATR {atr} not in 1.5-5.5")
    risk = r.get("risk_score", 0)
    if risk > 40: failures.append(f"Risk {risk} > 40")
    rr = r.get("risk_reward", 0)
    if rr < 2.2: failures.append(f"RR {rr} < 2.2")
    vol = r.get("volume_ratio", 0)
    if vol < 1.0: failures.append(f"Vol {vol} < 1.0")
    
    print(f"{sym} | Score: {score} | Failed: {', '.join(failures)}")
