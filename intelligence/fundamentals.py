"""
Fundamentals Engine — yfinance
--------------------------------
- P/E, P/B, ROE, ROA, EPS growth, D/E, promoter holding, free cash flow
- Scored on quality: low PE + high ROE + growing EPS + low D/E = bullish
- Fetched per-stock during analysis (yfinance caches internally)
"""

import logging
import yfinance as yf

log = logging.getLogger("screener")


def get_fundamentals_yf(symbol: str) -> dict:
    """
    Fetch fundamentals from yfinance for symbol (without .NS suffix).
    Returns dict with fund_score (0–32) and all fundamental fields.
    """
    try:
        info = yf.Ticker(symbol + ".NS").info
    except Exception as exc:
        log.debug("yfinance fundamentals failed for %s: %s", symbol, exc)
        return _empty_fundamentals()

    def safe(val, default=None):
        return val if val is not None and val == val else default  # NaN check

    pe      = safe(info.get("trailingPE"))
    pb      = safe(info.get("priceToBook"))
    roe     = safe(info.get("returnOnEquity"))
    roa     = safe(info.get("returnOnAssets"))
    rev_g   = safe(info.get("revenueGrowth"))
    earn_g  = safe(info.get("earningsGrowth"))
    de      = safe(info.get("debtToEquity"))
    promo   = safe(info.get("heldPercentInsiders"))
    mcap    = safe(info.get("marketCap"))
    fcf     = safe(info.get("freeCashflow"))
    fwd_eps = safe(info.get("forwardEps"))
    tr_eps  = safe(info.get("trailingEps"))
    sector  = info.get("sector", "Unknown") or "Unknown"
    industry = info.get("industry", "") or ""
    total_rev = safe(info.get("totalRevenue"))
    capex   = safe(info.get("capitalExpenditures"))
    fwd_pe  = safe(info.get("forwardPE"))

    # ── Scoring ──────────────────────────────────────────────────
    fund_score = 0

    if rev_g is not None and rev_g > 0.15:
        fund_score += 5     # >15% revenue growth
    if rev_g is not None and rev_g > 0.30:
        fund_score += 3     # >30% extra

    if earn_g is not None and earn_g > 0.20:
        fund_score += 8     # >20% earnings growth
    if earn_g is not None and earn_g > 0.40:
        fund_score += 4     # >40% extra

    if pe is not None and 5 < pe < 25:
        fund_score += 5     # Fair PE
    elif pe is not None and 25 <= pe < 40:
        fund_score += 2     # Growth PE acceptable

    if roe is not None and roe > 0.15:
        fund_score += 5     # >15% ROE = capital efficient
    if roe is not None and roe > 0.25:
        fund_score += 3     # >25% extra bonus

    if de is not None and de < 0.5:
        fund_score += 4     # Low debt
    elif de is not None and de < 1.0:
        fund_score += 2

    if promo is not None and promo > 0.55:
        fund_score += 3     # High promoter = skin in game
    if promo is not None and promo > 0.70:
        fund_score += 2     # Extra bonus

    if roa is not None and roa > 0.10:
        fund_score += 3     # Good asset utilization

    if fcf is not None and fcf > 0:
        fund_score += 2     # Positive free cash flow

    # Downside filters: highly leveraged or deteriorating earnings trap
    if de is not None and de > 2.5:
        fund_score -= 3  # High leverage risk
    if earn_g is not None and earn_g < 0 and (roe is None or roe < 0.10):
        fund_score -= 4  # Earnings declining + low ROE = value trap

    fund_score = min(max(fund_score, 0), 32)

    def fmt(v, mult=1, digits=1):
        if v is None:
            return None
        try:
            return round(float(v) * mult, digits)
        except Exception:
            return None

    return {
        "pe":              fmt(pe),
        "pb":              fmt(pb),
        "fwd_pe":          fmt(fwd_pe),
        "roe":             fmt(roe, 100),        # as %
        "roa":             fmt(roa, 100),
        "revenue_growth":  fmt(rev_g, 100),
        "earnings_growth": fmt(earn_g, 100),
        "debt_to_equity":  fmt(de),
        "promoter_pct":    fmt(promo, 100),
        "market_cap":      mcap,
        "free_cash_flow":  fcf,
        "total_revenue":   total_rev,
        "capex":           capex,
        "eps_fwd":         fmt(fwd_eps),
        "eps_trail":       fmt(tr_eps),
        "sector":          sector,
        "industry":        industry,
        "fund_score":      fund_score,
    }


def _empty_fundamentals() -> dict:
    return {
        "pe": None, "pb": None, "fwd_pe": None,
        "roe": None, "roa": None,
        "revenue_growth": None, "earnings_growth": None,
        "debt_to_equity": None, "promoter_pct": None,
        "market_cap": None, "free_cash_flow": None,
        "total_revenue": None, "capex": None,
        "eps_fwd": None, "eps_trail": None,
        "sector": "Unknown", "industry": "",
        "fund_score": 0,
    }
