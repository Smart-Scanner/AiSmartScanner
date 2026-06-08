import sqlite3
import json

# Import config thresholds
import sys
sys.path.append('c:\\Users\\91971\\Downloads\\smart-screener-deploy')
import config

conn = sqlite3.connect('cache/screener.db')
c = conn.cursor()
c.execute("SELECT data FROM scan_results")
rows = c.fetchall()

stocks = []
for (data_str,) in rows:
    try:
        d = json.loads(data_str)
        stocks.append(d)
    except: pass

# --- Audit 1: HC Attrition Funnel (Sequential) ---
universe = len(stocks)

# Helper to safely get nested values or defaults
def get_safe(d, key, default=0):
    v = d.get(key)
    if v is None: return default
    return v

# Funnel arrays
after_rsi = []
for s in stocks:
    rsi = get_safe(s, 'rsi')
    if config.HC_RSI_RANGE[0] <= rsi <= config.HC_RSI_RANGE[1]:
        after_rsi.append(s)

after_delivery = []
for s in after_rsi:
    dlv = get_safe(s, 'delivery_pct', 50.0) # default to 50 if missing delivery
    if dlv >= config.HC_DELIVERY_MIN:
        after_delivery.append(s)

after_atr = []
for s in after_delivery:
    atr = get_safe(s, 'atr_pct')
    if config.HC_ATR_RANGE[0] <= atr <= config.HC_ATR_RANGE[1]:
        after_atr.append(s)

after_risk = []
for s in after_atr:
    risk = get_safe(s, 'risk_score')
    if risk <= config.HC_RISK_MAX:
        after_risk.append(s)

after_rr = []
for s in after_risk:
    rr = get_safe(s, 'risk_reward')
    if rr >= config.HC_MIN_RISK_REWARD:
        after_rr.append(s)

after_macd = []
for s in after_rr:
    # MACD is disabled in R1 (False), so all pass
    macd = s.get('macd_signal')
    if not config.HC_REQUIRE_MACD_BULLISH or macd == 'Bullish':
        after_macd.append(s)

after_volume = []
for s in after_macd:
    vol = get_safe(s, 'volume_ratio', 1.0)
    if vol >= config.HC_REQUIRE_VOLUME:
        after_volume.append(s)

after_signals = []
for s in after_volume:
    signals = s.get('signals', [])
    bullish = sum(1 for x in signals if x[2] == 'bullish')
    if bullish >= config.HC_MIN_SIGNALS_BULLISH:
        after_signals.append(s)

after_score = []
for s in after_signals:
    score = get_safe(s, 'score')
    if score >= config.HC_MIN_SCORE:
        after_score.append(s)

print("=== FUNNEL AUDIT ===")
print(f"Universe: {universe}")
print(f"RSI: {len(after_rsi)}")
print(f"Delivery: {len(after_delivery)}")
print(f"ATR: {len(after_atr)}")
print(f"Risk: {len(after_risk)}")
print(f"RR: {len(after_rr)}")
print(f"MACD: {len(after_macd)}")
print(f"Volume: {len(after_volume)}")
print(f"Signals: {len(after_signals)}")
print(f"Score: {len(after_score)} (Final HC)")

# --- Audit 2: Top 20 Near-Miss HC Audit ---
print("\n=== TOP 20 NEAR-MISS AUDIT ===")

# Sort universe by score descending
stocks_sorted = sorted(stocks, key=lambda x: get_safe(x, 'score', 0), reverse=True)

for i, s in enumerate(stocks_sorted[:20]):
    sym = s.get('symbol')
    score = get_safe(s, 'score')
    risk = get_safe(s, 'risk_score')
    vol = get_safe(s, 'volume_ratio', 1.0)
    dlv = get_safe(s, 'delivery_pct', 50.0)
    atr = get_safe(s, 'atr_pct')
    rr = get_safe(s, 'risk_reward')
    macd = s.get('macd_signal', 'Unknown')
    rsi = get_safe(s, 'rsi')
    
    signals = s.get('signals', [])
    bullish = sum(1 for x in signals if x[2] == 'bullish')
    
    fails = []
    if score < config.HC_MIN_SCORE: fails.append("Score")
    if not (config.HC_RSI_RANGE[0] <= rsi <= config.HC_RSI_RANGE[1]): fails.append("RSI")
    if dlv < config.HC_DELIVERY_MIN: fails.append("Delivery")
    if not (config.HC_ATR_RANGE[0] <= atr <= config.HC_ATR_RANGE[1]): fails.append("ATR")
    if risk > config.HC_RISK_MAX: fails.append("Risk")
    if rr < config.HC_MIN_RISK_REWARD: fails.append("RR")
    if config.HC_REQUIRE_MACD_BULLISH and macd != 'Bullish': fails.append("MACD")
    if vol < config.HC_REQUIRE_VOLUME: fails.append("Volume")
    if bullish < config.HC_MIN_SIGNALS_BULLISH: fails.append("Signals")
    
    print(f"{sym.ljust(15)} | Score: {score} | Fails: {', '.join(fails) if fails else 'NONE (HC)'}")
    print(f"  RSI: {rsi:.1f} | Dlv: {dlv:.1f} | ATR: {atr:.2f} | Risk: {risk} | RR: {rr:.1f} | Vol: {vol:.1f} | Sig: {bullish}")
    
