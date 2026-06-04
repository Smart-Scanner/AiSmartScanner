"""
News Sentiment Engine — Waterfall
-----------------------------------
Waterfall order:
1. GDELT + FinBERT (bulk cache, O(1) lookup) — primary
2. Finnhub (60/min, unlimited/day) — top stock enrichment [NOT IMPLEMENTED — add FINNHUB_API_KEY]
3. yfinance .news — zero-limit fallback with keyword scoring
4. MarketAux (100/day) — supplement for high-score stocks
5. NewsAPI (100/day) — global macro headlines only

Rate limiting: MarketAux and NewsAPI have day quotas.
Both are capped at 80 calls/day to preserve buffer.
"""

import os
import time
import logging
import requests
import threading
from intelligence.news_gdelt_finbert import get_gdelt_sentiment

log = logging.getLogger("screener")

MARKETAUX_KEY = os.getenv("MARKETAUX_API_KEY", "")
NEWS_API_KEY  = os.getenv("NEWS_API_KEY", "")
FINNHUB_KEY   = os.getenv("FINNHUB_API_KEY", "")

# Day quota tracking
_quota_lock = threading.Lock()
_newsapi_calls = 0
_MARKETAUX_DAILY_CAP = 50
_NEWSAPI_DAILY_CAP = 80

# Reset counter daily (simple time-based reset)
_quota_reset_at = time.time() + 86400  # 24h from server start


def _check_reset():
    global _newsapi_calls, _quota_reset_at
    if time.time() > _quota_reset_at:
        _newsapi_calls = 0
        _quota_reset_at = time.time() + 86400


def _get_marketaux_calls_today() -> int:
    import db
    today_str = datetime.now().strftime("%Y-%m-%d")
    last_date = db.get_meta("marketaux_calls_date")
    if last_date != today_str:
        db.set_meta("marketaux_calls_date", today_str)
        db.set_meta("marketaux_calls_count", 0)
        return 0
    return db.get_meta("marketaux_calls_count", 0)


def _increment_marketaux_calls():
    import db
    today_str = datetime.now().strftime("%Y-%m-%d")
    count = _get_marketaux_calls_today()
    db.set_meta("marketaux_calls_date", today_str)
    db.set_meta("marketaux_calls_count", count + 1)


def _keyword_score(text: str) -> float:
    """Keyword-based fallback sentiment: -1 to +1."""
    text = text.lower()
    pos = ["profit", "growth", "order", "contract", "win", "beat", "surge", "record",
           "expansion", "buyback", "dividend", "upgrade", "rally", "strong", "positive",
           "revenue up", "awarded", "new high", "outperform"]
    neg = ["loss", "decline", "miss", "fraud", "penalty", "default", "bankruptcy",
           "layoff", "cut", "below", "weak", "negative", "selloff", "probe", "fine"]
    s = sum(1 for w in pos if w in text) - sum(1 for w in neg if w in text)
    return max(-1.0, min(1.0, s * 0.25))


def _fetch_yfinance_news(symbol: str) -> tuple:
    """Zero-limit fallback: yfinance .news with keyword scoring."""
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol + ".NS")
        news = tk.news or []
        if not news:
            return 0, []
        headlines = [n.get("title", "") for n in news[:10] if n.get("title")]
        scores = [_keyword_score(h) for h in headlines]
        avg = sum(scores) / len(scores) if scores else 0
        items = [{"title": h, "score": round(s, 2), "source": "yfinance"}
                 for h, s in zip(headlines, scores)]
        news_score = round(avg * 10, 1)  # -10 to +10
        return news_score, items[:5]
    except Exception as exc:
        log.debug("yfinance news failed for %s: %s", symbol, exc)
        return 0, []


from datetime import datetime

def _fetch_marketaux(symbol: str) -> tuple:
    """MarketAux per-stock sentiment (50/day quota)."""
    if not MARKETAUX_KEY:
        return None, []
        
    try:
        calls_today = _get_marketaux_calls_today()
        if calls_today >= _MARKETAUX_DAILY_CAP:
            log.warning("MarketAux daily quota limit of %d reached.", _MARKETAUX_DAILY_CAP)
            return None, []

        _increment_marketaux_calls()

        url = (f"https://api.marketaux.com/v1/news/all"
               f"?symbols={symbol}.NSE&filter_entities=true"
               f"&language=en&api_token={MARKETAUX_KEY}&limit=5")
        resp = requests.get(url, timeout=6)
        
        # Catch rate limit or billing issues and stop making calls
        if resp.status_code in (429, 402, 403):
            log.warning("MarketAux API returned error status %d. Capping quota for today.", resp.status_code)
            import db
            today_str = datetime.now().strftime("%Y-%m-%d")
            db.set_meta("marketaux_calls_count", _MARKETAUX_DAILY_CAP)
            return None, []
            
        data = resp.json().get("data", [])
        if not data:
            return 0.0, []
            
        sents = [a.get("sentiment_score", 0) for a in data]
        items = [{"title": a.get("title", ""),
                  "score": round(a.get("sentiment_score", 0), 3),
                  "date":  a.get("published_at", "")[:10],
                  "source": "marketaux"}
                 for a in data]
        avg = sum(sents) / len(sents) if sents else 0
        score = round(avg * 10, 1)
        return score, items
    except Exception as exc:
        log.debug("MarketAux failed for %s: %s", symbol, exc)
        return None, []


def _fetch_finnhub(symbol: str) -> tuple:
    """Finnhub per-stock news — 60/min, unlimited/day."""
    if not FINNHUB_KEY:
        return None, []
    try:
        from datetime import datetime, timedelta
        now = datetime.now()
        from_dt = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        to_dt = now.strftime("%Y-%m-%d")
        url = (f"https://finnhub.io/api/v1/company-news"
               f"?symbol={symbol}.NS&from={from_dt}&to={to_dt}&token={FINNHUB_KEY}")
        data = requests.get(url, timeout=6).json()
        if not data or not isinstance(data, list):
            return None, []
        headlines = [a.get("headline", "") for a in data[:10] if a.get("headline")]
        scores = [_keyword_score(h) for h in headlines]
        avg = sum(scores) / len(scores) if scores else 0
        items = [{"title": h, "score": round(s, 2), "source": "finnhub"}
                 for h, s in zip(headlines, scores)]
        return round(avg * 10, 1), items[:5]
    except Exception as exc:
        log.debug("Finnhub failed for %s: %s", symbol, exc)
        return None, []


def fetch_news_sentiment(symbol: str, query_marketaux: bool = False) -> tuple:
    """
    Master news sentiment function.
    Returns (score: float, items: list).
    Score range: roughly -15 to +15.

    Waterfall:
    1. GDELT + FinBERT cache (O(1))
    2. Finnhub (if key set)
    3. MarketAux (if query_marketaux and quota available)
    4. yfinance fallback (always)
    """
    # 1. GDELT primary (always first)
    gdelt_score, gdelt_articles, spike = get_gdelt_sentiment(symbol)

    # 2. Finnhub supplement
    fh_score, fh_items = _fetch_finnhub(symbol)

    # 3. MarketAux supplement (only if allowed and GDELT found nothing significant)
    mx_score, mx_items = None, []
    if query_marketaux and abs(gdelt_score) < 2 and (fh_score is None or abs(fh_score or 0) < 2):
        mx_score, mx_items = _fetch_marketaux(symbol)

    # 4. Fallback
    yf_score, yf_items = 0, []
    if gdelt_score == 0 and fh_score is None and mx_score is None:
        yf_score, yf_items = _fetch_yfinance_news(symbol)

    # Combine scores (GDELT is primary, others supplement)
    scores = [s for s in [gdelt_score, fh_score, mx_score] if s is not None]
    if scores:
        final_score = gdelt_score * 0.6 + (sum(scores[1:]) / max(1, len(scores[1:]))) * 0.4 if len(scores) > 1 else gdelt_score
    else:
        final_score = yf_score

    # Merge articles
    all_items = gdelt_articles[:3] + (fh_items or [])[:2] + mx_items[:2]

    # Spike bonus: >3x news volume → extra signal
    if spike > 3:
        final_score += 5
    elif spike > 1.5:
        final_score += 2

    return round(final_score, 2), all_items[:6]


def get_global_headlines() -> list:
    """
    NewsAPI global macro headlines (not per-stock).
    Used for macro context display in dashboard.
    """
    global _newsapi_calls
    with _quota_lock:
        _check_reset()
        if _newsapi_calls >= _NEWSAPI_DAILY_CAP or not NEWS_API_KEY:
            return []
        _newsapi_calls += 1

    try:
        url = (f"https://newsapi.org/v2/top-headlines"
               f"?category=business&language=en&pageSize=10&apiKey={NEWS_API_KEY}")
        data = requests.get(url, timeout=6).json()
        articles = data.get("articles", [])
        return [{"title": a.get("title", ""),
                 "source": a.get("source", {}).get("name", ""),
                 "published": a.get("publishedAt", "")[:10]}
                for a in articles[:10]]
    except Exception as exc:
        log.debug("NewsAPI global headlines failed: %s", exc)
        return []
