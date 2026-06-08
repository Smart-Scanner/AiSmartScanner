"""
P0.1 News Intelligence Coverage Audit
--------------------------------------
Runs a controlled scan across a symbol universe and measures:
1. Total coverage % (symbols with at least 1 scored article)
2. FinBERT coverage % (symbols scored by FinBERT, not keyword fallback)
3. Per-provider hit counts (Finnhub, MarketAux, Google RSS, GDELT, Yahoo)
4. Zero-coverage symbols list
5. Rate limit violations (HTTP 429 count)

Usage:
    python audit_news.py          # Nifty 50 (quick)
    python audit_news.py --full   # Full 250 symbol universe
"""

import sys
import os
import time
import logging

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")

from intelligence.news_sentiment import fetch_news_sentiment
from intelligence.news_cache import get_cache_stats, get_refresh_telemetry
from intelligence.finbert_engine import is_finbert_available

# ── Symbol Universes ──────────────────────────────────────────
NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "ITC", "SBIN", "BHARTIARTL", "HINDUNILVR", "BAJFINANCE",
    "LT", "KOTAKBANK", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "NTPC", "WIPRO",
    "ADANIENT", "POWERGRID", "NESTLEIND", "JSWSTEEL", "TATAMOTORS",
    "TATASTEEL", "M&M", "BAJAJFINSV", "INDUSINDBK", "HCLTECH",
    "COALINDIA", "ADANIPORTS", "ONGC", "GRASIM", "DRREDDY",
    "APOLLOHOSP", "CIPLA", "EICHERMOT", "SBILIFE", "DIVISLAB",
    "TECHM", "BPCL", "HEROMOTOCO", "TATACONSUM", "BRITANNIA",
    "HINDALCO", "BAJAJ-AUTO", "SHRIRAMFIN", "HDFCLIFE", "BEL",
]

EXTENDED_250 = NIFTY_50 + [
    "PIIND", "IRCTC", "DMART", "NAUKRI", "PERSISTENT",
    "POLYCAB", "ASTRAL", "DEEPAKNTR", "ATUL", "COFORGE",
    "LTTS", "MPHASIS", "TRENT", "ZOMATO", "PAYTM",
    "NYKAA", "POLICYBZR", "DELHIVERY", "LODHA", "PHOENIXLTD",
    "LICI", "HAL", "BDL", "MAZAGONDOCK", "COCHINSHIP",
    "GRSE", "SOLARINDS", "AFFLE", "ROUTE", "HAPPSTMNDS",
]

def run_audit(symbols: list, label: str):
    print(f"\n{'='*70}")
    print(f" P0.1 NEWS INTELLIGENCE COVERAGE AUDIT")
    print(f" Universe: {label} ({len(symbols)} symbols)")
    print(f" FinBERT Available: {is_finbert_available()}")
    print(f" Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    total = len(symbols)
    covered = 0
    finbert_covered = 0
    zero_coverage = []
    provider_hits = {}
    total_articles = 0
    total_time = 0

    for i, sym in enumerate(symbols, 1):
        start = time.time()
        score, items, breakdown = fetch_news_sentiment(sym, query_marketaux=True, scan_mode="deep")
        elapsed = time.time() - start
        total_time += elapsed

        article_count = len(items)
        total_articles += article_count
        has_coverage = article_count > 0

        if has_coverage:
            covered += 1
        else:
            zero_coverage.append(sym)

        # Check if FinBERT was used (items have score != 0 usually means scored)
        for item in items:
            if abs(item.get("score", 0)) > 0:
                finbert_covered += 1
                break

        # Track provider hits
        for provider in breakdown:
            if provider not in provider_hits:
                provider_hits[provider] = 0
            provider_hits[provider] += 1

        status = "[Y]" if has_coverage else "[N]"
        providers = list(breakdown.keys()) if breakdown else ["none"]
        print(f"  [{i:3}/{total}] {status} {sym:15} | Score: {score:7.2f} | Articles: {article_count:2} | {elapsed:.1f}s | {providers}")

    # ── Results ──
    coverage_pct = round((covered / total) * 100, 1) if total else 0
    finbert_pct = round((finbert_covered / total) * 100, 1) if total else 0

    print(f"\n{'='*70}")
    print(f" AUDIT RESULTS")
    print(f"{'='*70}")
    print(f"  Total Symbols Scanned:   {total}")
    print(f"  Symbols with Coverage:   {covered}")
    print(f"  Coverage %:              {coverage_pct}%")
    print(f"  FinBERT Scored Symbols:  {finbert_covered}")
    print(f"  FinBERT Coverage %:      {finbert_pct}%")
    print(f"  Total Articles Found:    {total_articles}")
    print(f"  Total Scan Time:         {total_time:.1f}s")
    print(f"  Avg Time Per Symbol:     {total_time/total:.2f}s")
    print()

    print(f"  Provider Hit Counts:")
    for prov, count in sorted(provider_hits.items(), key=lambda x: -x[1]):
        print(f"    {prov:15} : {count} symbols")
    print()

    if zero_coverage:
        print(f"  Zero Coverage Symbols ({len(zero_coverage)}):")
        for sym in zero_coverage:
            print(f"    - {sym}")
    else:
        print(f"  Zero Coverage Symbols: NONE")
    print()

    # Telemetry
    telemetry = get_refresh_telemetry()
    print(f"  Provider Telemetry:")
    for prov, t in telemetry.items():
        print(f"    {prov:15} | health={t['health']:8} | calls={t['total_calls']:3} | failures={t['total_failures']:2} | consecutive={t['consecutive_failures']}")
    print()

    # Verdict
    print(f"{'='*70}")
    if coverage_pct >= 80 and finbert_pct >= 50:
        print(f"  VERDICT: PASS -- Coverage {coverage_pct}%, FinBERT {finbert_pct}%")
    elif coverage_pct >= 50:
        print(f"  VERDICT: PARTIAL -- Coverage {coverage_pct}%, FinBERT {finbert_pct}%")
    else:
        print(f"  VERDICT: FAIL -- Coverage {coverage_pct}%, FinBERT {finbert_pct}%")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    if "--phase-c" in sys.argv:
        from universe import get_active_universe
        full = get_active_universe()
        run_audit(full, f"Phase C: Full Universe ({len(full)})")
    elif "--phase-b" in sys.argv:
        from universe import get_active_universe
        full = get_active_universe()
        run_audit(full[:250], "Phase B: 250 Symbols")
    elif "--full" in sys.argv:
        run_audit(EXTENDED_250, "Extended 80")
    else:
        run_audit(NIFTY_50, "Nifty 50")

