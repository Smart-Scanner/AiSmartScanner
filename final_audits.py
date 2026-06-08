import sqlite3
import json
import pandas as pd

conn = sqlite3.connect('cache/screener.db')
c = conn.cursor()
c.execute("SELECT data FROM scan_results")
rows = c.fetchall()

stocks = []
for (data_str,) in rows:
    try:
        stocks.append(json.loads(data_str))
    except: pass

def safe_get(d, k, default=0):
    v = d.get(k)
    return default if v is None else v

# --- Audit 1: Overlap Audit ---
# Calculate composite execution rank
df_exec = pd.DataFrame([{
    'symbol': s.get('symbol'),
    'score': safe_get(s, 'score'),
    'vol': safe_get(s, 'volume_ratio', 1.0),
    'dlv': safe_get(s, 'delivery_pct', 50.0),
    'risk': safe_get(s, 'risk_score'),
    'rr': safe_get(s, 'risk_reward')
} for s in stocks])

df_exec['vol_rank'] = df_exec['vol'].rank(ascending=False)
df_exec['dlv_rank'] = df_exec['dlv'].rank(ascending=False)
df_exec['risk_rank'] = df_exec['risk'].rank(ascending=True) # lower is better
df_exec['rr_rank'] = df_exec['rr'].rank(ascending=False)
df_exec['exec_score'] = df_exec['vol_rank'] + df_exec['dlv_rank'] + df_exec['risk_rank'] + df_exec['rr_rank']

# Top 50 by Score
top50_score = df_exec.sort_values('score', ascending=False).head(50)
top50_score_syms = set(top50_score['symbol'])

# Top 50 by Execution Quality (lowest exec_score rank sum)
top50_exec = df_exec.sort_values('exec_score', ascending=True).head(50)
top50_exec_syms = set(top50_exec['symbol'])

overlap = top50_score_syms.intersection(top50_exec_syms)

print("=== OVERLAP AUDIT ===")
print(f"Intersection Count: {len(overlap)}")
if overlap:
    common_df = df_exec[df_exec['symbol'].isin(overlap)]
    print(f"Average Score of Common: {common_df['score'].mean():.1f}")
    print(f"Average Vol of Common: {common_df['vol'].mean():.1f}")
    print(f"Average Dlv of Common: {common_df['dlv'].mean():.1f}")
    print(f"Average Risk of Common: {common_df['risk'].mean():.1f}")
    print(f"Common Stocks: {', '.join(overlap)}")
else:
    print("Zero overlap between high-score and high-execution populations.")

# Near-Overlap Candidates (High score stocks sorted by execution rank)
near_overlap = top50_score.sort_values('exec_score', ascending=True).head(10)
print("\nTop 10 Near-Overlap Candidates (from Top 50 Score list, best execution):")
for _, r in near_overlap.iterrows():
    print(f"{r['symbol'].ljust(15)} | Score: {r['score']} | Vol: {r['vol']:.1f} | Dlv: {r['dlv']:.1f} | Risk: {r['risk']:.0f} | RR: {r['rr']:.1f} | ExecRank: {r['exec_score']:.0f}")


# --- Audit 2: Fundamental Coverage ---
FUND_FIELDS = ("pe", "pb", "roe", "roa", "revenue_growth", "earnings_growth", "debt_to_equity")
missing_count = 0
missing_syms = []

for s in stocks:
    f = s.get('fundamentals', {})
    if not f or all(f.get(k) is None for k in FUND_FIELDS):
        missing_count += 1
        missing_syms.append(s.get('symbol'))

print("\n=== COVERAGE AUDIT ===")
print(f"Universe: {len(stocks)}")
print(f"Fundamental Available: {len(stocks) - missing_count}")
print(f"Fundamental Missing: {missing_count}")
print(f"Coverage %: {((len(stocks) - missing_count) / len(stocks)) * 100:.1f}%")
print(f"Top 20 missing symbols: {', '.join(missing_syms[:20])}")


# --- Audit 3: Score Component Contribution ---
print("\n=== COMPONENT AUDIT (Top 50 Stocks Averages) ===")
comp_sums = {
    'technical': 0, 'earnings_momentum': 0, 'fundamental': 0, 
    'smart_money': 0, 'sector_rotation': 0, 'news_sentiment': 0,
    'news_spike': 0, 'macro': 0, 'catalyst': 0
}

for sym in top50_score_syms:
    s = next(x for x in stocks if x.get('symbol') == sym)
    comps = s.get('_score_components', {})
    for k in comp_sums.keys():
        comp_sums[k] += comps.get(k, 0)

for k, v in comp_sums.items():
    print(f"{k.ljust(20)}: {v / 50:.2f} avg contribution")
