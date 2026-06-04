"""
Multi-Timeframe Trend Alignment Engine
----------------------------------------
FIXED:
- ema50_w: clamped to max(5, ...) so minimum EMA window is always valid
- Weighted scoring: 1M=+3/-3, 1W=+2/-2, 1D=+1/-1 (monthly most reliable)
- Monthly UNKNOWN is not penalized (sparse data expected)
"""

import logging
import yfinance as yf
from ta.trend import EMAIndicator

log = logging.getLogger("screener")

TIMEFRAMES = {
    "1D": ("6mo",  "1d"),
    "1W": ("2y",   "1wk"),
    "1M": ("5y",   "1mo"),
}

# Higher timeframes carry more weight: 1M > 1W > 1D
MTF_SCORE_MAP = {
    "1D": {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0, "UNKNOWN": 0},
    "1W": {"BULLISH": 2, "BEARISH": -2, "NEUTRAL": 0, "UNKNOWN": 0},
    "1M": {"BULLISH": 3, "BEARISH": -3, "NEUTRAL": 0, "UNKNOWN": 0},  # monthly UNKNOWN is OK
}


def get_mtf_trend(symbol: str) -> tuple:
    """
    Returns (trends_dict, mtf_score).
    trends_dict: {"1D": "BULLISH"|"BEARISH"|"NEUTRAL"|"UNKNOWN", ...}
    mtf_score: weighted sum — monthly most reliable (max +6 / min -6)
    """
    ns = symbol + ".NS"
    trends = {}

    for tf, (period, interval) in TIMEFRAMES.items():
        try:
            df = yf.download(ns, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 20:
                trends[tf] = "UNKNOWN"
                continue

            close = df["Close"].squeeze()
            last = float(close.iloc[-1])

            ema20 = float(EMAIndicator(close, window=20).ema_indicator().iloc[-1])

            # Clamp EMA50 window: minimum 5 (never 1 or 0 on short series)
            ema50_w = max(5, min(50, len(close) - 1))
            ema50 = float(EMAIndicator(close, window=ema50_w).ema_indicator().iloc[-1])

            if last > ema20 > ema50:
                trends[tf] = "BULLISH"
            elif last < ema20 < ema50:
                trends[tf] = "BEARISH"
            else:
                trends[tf] = "NEUTRAL"

        except Exception as exc:
            log.debug("MTF %s %s failed: %s", symbol, tf, exc)
            trends[tf] = "UNKNOWN"

    # Weighted score: higher timeframes count more
    mtf_score = sum(
        MTF_SCORE_MAP[tf].get(state, 0)
        for tf, state in trends.items()
    )

    return trends, mtf_score
