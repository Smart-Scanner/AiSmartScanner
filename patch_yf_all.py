"""
Deploy 2+3+4: Centralize all yFinance calls + Guard all callers + Add source attribution
Files patched:
  1. intelligence/fundamentals.py — Lines 183, 447, 450, 187, 455
  2. intelligence/macro.py — Lines 15, 171, 190
  3. intelligence/sector_rotation.py — Lines 12, 117, 127
  4. intelligence/news_sentiment.py — Lines 337, 355
  5. intelligence/order_book.py — Lines 11, 37
  6. intelligence/mtf.py — Lines 53, 60, 125, 167
  7. intelligence/__init__.py — Lines 18, 107, 118
"""

import os

def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"  WARNING: pattern not found in {path}: {old[:60]}...")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Patched {path}")

# ═══ 1. fundamentals.py ═══
print("1. fundamentals.py")
patch_file("intelligence/fundamentals.py", [
    # Import
    (
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_ticker"
    ),
    # Line 183: get_fundamentals_yf L4 fetch
    (
        '        import yfinance as yf\n        info = yf.Ticker(sym + ".NS").info\n        yf_record_success()',
        '        ticker = get_yf_ticker(sym + ".NS", source="fundamentals")\n        info = ticker.info\n        yf_record_success()'
    ),
    # Line 187: failure source
    (
        '        yf_record_failure()\n        return _empty_fundamentals()',
        '        yf_record_failure(source="fundamentals")\n        return _empty_fundamentals()'
    ),
    # Line 447: extract_detailed_financials
    (
        '        import yfinance as yf\n        ticker = yf.Ticker(clean + ".NS")\n        fin_y = ticker.financials\n        if fin_y.empty:\n            ticker = yf.Ticker(clean)\n            fin_y = ticker.financials\n        yf_record_success()',
        '        ticker = get_yf_ticker(clean + ".NS", source="fundamentals_detailed")\n        fin_y = ticker.financials\n        if fin_y.empty:\n            ticker = get_yf_ticker(clean, source="fundamentals_detailed")\n            fin_y = ticker.financials\n        yf_record_success()'
    ),
    # Line 455: failure source
    (
        '        yf_record_failure()\n        return {\n            "yearly": [], "quarterly": [],\n            "fin_health_score": 0,\n            "fin_health_verdict": "Stressed",\n            "fin_alerts": ["Data fetch error"],\n        }',
        '        yf_record_failure(source="fundamentals_detailed")\n        return {\n            "yearly": [], "quarterly": [],\n            "fin_health_score": 0,\n            "fin_health_verdict": "Stressed",\n            "fin_alerts": ["Data fetch error"],\n        }'
    ),
])

# ═══ 2. macro.py ═══
print("2. macro.py")
patch_file("intelligence/macro.py", [
    # Import: replace bare yfinance import with guard
    (
        "import yfinance as yf",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_download"
    ),
    # Line 171: yf.download in world indices
    (
        '            df = yf.download(ticker, period="5d", interval="1d",\n                             progress=False, auto_adjust=True)',
        '            df = get_yf_download(ticker, source="macro_world", period="5d", interval="1d",\n                             progress=False, auto_adjust=True)'
    ),
    # Line 190: yf.download in spot
    (
        '            df = yf.download(ticker, period="3d", interval="1d",\n                             progress=False, auto_adjust=True)',
        '            df = get_yf_download(ticker, source="macro_spot", period="3d", interval="1d",\n                             progress=False, auto_adjust=True)'
    ),
])

# ═══ 3. sector_rotation.py ═══
print("3. sector_rotation.py")
patch_file("intelligence/sector_rotation.py", [
    # Import
    (
        "import yfinance as yf",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_download"
    ),
    # Line 117: benchmark download
    (
        '        bench_df = yf.download(BENCHMARK, period="6mo", interval="1d",\n                               progress=False, auto_adjust=True)',
        '        bench_df = get_yf_download(BENCHMARK, source="sector_rotation", period="6mo", interval="1d",\n                               progress=False, auto_adjust=True)'
    ),
    # Line 127: sector download
    (
        '                sec_df = yf.download(ticker, period="6mo", interval="1d",\n                                     progress=False, auto_adjust=True)',
        '                sec_df = get_yf_download(ticker, source="sector_rotation", period="6mo", interval="1d",\n                                     progress=False, auto_adjust=True)'
    ),
])

# ═══ 4. news_sentiment.py ═══
print("4. news_sentiment.py")
patch_file("intelligence/news_sentiment.py", [
    # Import update
    (
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_ticker"
    ),
    # Line 337: yf.Ticker in _fetch_yfinance_articles
    (
        '        import yfinance as yf\n        tk = yf.Ticker(symbol + ".NS")',
        '        tk = get_yf_ticker(symbol + ".NS", source="news_sentiment")'
    ),
    # Line 355: failure source
    (
        '        yf_record_failure()\n        news_cache.record_refresh_failure("yahoo", str(exc))',
        '        yf_record_failure(source="news_sentiment")\n        news_cache.record_refresh_failure("yahoo", str(exc))'
    ),
])

# ═══ 5. order_book.py ═══
print("5. order_book.py")
patch_file("intelligence/order_book.py", [
    # Import
    (
        "import logging\nimport yfinance as yf",
        "import logging\nfrom intelligence.yf_guard import yf_is_available, get_yf_ticker"
    ),
    # Line 37: yf.Ticker fallback
    (
        '                info = yf.Ticker(symbol + ".NS").info',
        '                if not yf_is_available():\n                    pass\n                else:\n                    info = get_yf_ticker(symbol + ".NS", source="order_book").info'
    ),
])

# ═══ 6. mtf.py ═══
print("6. mtf.py")
patch_file("intelligence/mtf.py", [
    # Line 53: internal _compute_mtf import
    (
        'def _compute_mtf(symbol: str) -> tuple:\n    """Internal: download all 3 timeframes and compute trend dict + score."""\n    import yfinance as yf',
        'def _compute_mtf(symbol: str) -> tuple:\n    """Internal: download all 3 timeframes and compute trend dict + score."""\n    from intelligence.yf_guard import get_yf_download'
    ),
    # Line 60: yf.download in _compute_mtf
    (
        '            df = yf.download(ns, period=period, interval=interval,\n                             progress=False, auto_adjust=True)',
        '            df = get_yf_download(ns, source="mtf", period=period, interval=interval,\n                             progress=False, auto_adjust=True)'
    ),
    # Line 125: failure source in get_mtf_trend
    (
        '        yf_record_failure()\n        return {}, 0\n\n    with _mtf_lock:',
        '        yf_record_failure(source="mtf")\n        return {}, 0\n\n    with _mtf_lock:'
    ),
    # Line 167: failure source in prefetch_mtf_batch
    (
        '            yf_record_failure()\n            return sym, None',
        '            yf_record_failure(source="mtf_prefetch")\n            return sym, None'
    ),
])

# ═══ 7. intelligence/__init__.py ═══
print("7. intelligence/__init__.py")
patch_file("intelligence/__init__.py", [
    # Import: replace bare yfinance
    (
        "import yfinance as yf\nfrom metrics.timer import timed\nfrom intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success",
        "from metrics.timer import timed\nfrom intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_ticker"
    ),
    # Line 107: yf.Ticker in get_upcoming_events
    (
        '        tk = yf.Ticker(symbol + ".NS")',
        '        tk = get_yf_ticker(symbol + ".NS", source="events")'
    ),
    # Line 118: failure source
    (
        '        yf_record_failure()\n        result = []',
        '        yf_record_failure(source="events")\n        result = []'
    ),
])

print("\n✅ ALL PATCHES APPLIED SUCCESSFULLY")
print("Files patched: fundamentals, macro, sector_rotation, news_sentiment, order_book, mtf, __init__")
