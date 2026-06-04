"""
Sector Rotation Engine — RRG (Relative Rotation Graph) Proxy
--------------------------------------------------------------
- Computes RS Ratio + RS Momentum for 12 Nifty sector indices vs NSEI
- Classifies into LEADING / IMPROVING / WEAKENING / LAGGING quadrants
- Cached globally, refreshed each scan (or every 1 hour)
"""

import time
import logging
import threading
import yfinance as yf
import pandas as pd

log = logging.getLogger("screener")

BENCHMARK = "^NSEI"

NIFTY_SECTORS = {
    "Bank Nifty":    "^NSEBANK",
    "Nifty IT":      "^CNXIT",
    "Nifty Pharma":  "^CNXPHARMA",
    "Nifty Auto":    "^CNXAUTO",
    "Nifty FMCG":    "^CNXFMCG",
    "Nifty Metal":   "^CNXMETAL",
    "Nifty Realty":  "^CNXREALTY",
    "Nifty Energy":  "^CNXENERGY",
    "Nifty Infra":   "^CNXINFRA",
    "Nifty PSU":     "^CNXPSUBANK",
    "Nifty Media":   "^CNXMEDIA",
    "Nifty Midcap":  "^NSEMDCP50",
}

# sector string → Nifty index name — ordered from most specific to least specific
# industrial/cement/defence removed from wrong mappings
SECTOR_TO_NIFTY = {
    # Banking / Finance
    "banking":          "Bank Nifty",
    "bank":             "Bank Nifty",
    "finance":          "Bank Nifty",
    "nbfc":             "Bank Nifty",
    # IT / Software
    "information technology": "Nifty IT",
    "technology":       "Nifty IT",
    "software":         "Nifty IT",
    "it":               "Nifty IT",
    # Pharma / Healthcare
    "pharmaceutical":   "Nifty Pharma",
    "healthcare":       "Nifty Pharma",
    "pharma":           "Nifty Pharma",
    "hospital":         "Nifty Pharma",
    # Auto
    "automobile":       "Nifty Auto",
    "automotive":       "Nifty Auto",
    "auto":             "Nifty Auto",
    # FMCG / Consumer
    "fmcg":             "Nifty FMCG",
    "consumer":         "Nifty FMCG",
    "household":        "Nifty FMCG",
    # Metals
    "metals":           "Nifty Metal",
    "steel":            "Nifty Metal",
    "mining":           "Nifty Metal",
    "aluminium":        "Nifty Metal",
    # Realty
    "real estate":      "Nifty Realty",
    "realty":           "Nifty Realty",
    # Energy
    "energy":           "Nifty Energy",
    "oil":              "Nifty Energy",
    "gas":              "Nifty Energy",
    # Infra (specific — railways/capital goods excluded)
    "infrastructure":   "Nifty Infra",
    "infra":            "Nifty Infra",
    # Media
    "media":            "Nifty Media",
    "entertainment":    "Nifty Media",
    "broadcast":        "Nifty Media",
}

sector_rotation_cache: dict = {}
_rrg_lock = threading.Lock()
_rrg_built_at: float = 0
_RRG_TTL = 3600  # 1 hour
_rrg_running = False  # Prevent concurrent scans


def compute_rrg_quadrant(rs_ratio: float, rs_momentum: float) -> str:
    if rs_ratio > 100 and rs_momentum > 100:
        return "LEADING 🟢"
    elif rs_ratio < 100 and rs_momentum > 100:
        return "IMPROVING 🟡"
    elif rs_ratio > 100 and rs_momentum < 100:
        return "WEAKENING 🟠"
    else:
        return "LAGGING 🔴"


def scan_sector_rotation():
    """
    Compute RRG for all 12 Nifty sector indices vs NSEI benchmark.
    Populates global sector_rotation_cache.
    """
    global sector_rotation_cache, _rrg_built_at, _rrg_running

    now = time.time()
    if now - _rrg_built_at < _RRG_TTL and sector_rotation_cache:
        return
    if _rrg_running:
        return

    _rrg_running = True
    log.info("Computing sector rotation (RRG)...")
    results = {}

    try:
        bench_df = yf.download(BENCHMARK, period="6mo", interval="1d",
                               progress=False, auto_adjust=True)
        if bench_df.empty or len(bench_df) < 20:
            log.warning("Benchmark data empty, skipping RRG")
            _rrg_running = False
            return
        bench = bench_df["Close"].squeeze()

        for name, ticker in NIFTY_SECTORS.items():
            try:
                sec_df = yf.download(ticker, period="6mo", interval="1d",
                                     progress=False, auto_adjust=True)
                if sec_df.empty or len(sec_df) < 20:
                    continue
                sec = sec_df["Close"].squeeze()

                # Align indices
                aligned = pd.concat([sec, bench], axis=1, keys=["sec", "bench"]).dropna()
                if len(aligned) < 20:
                    continue

                rs = aligned["sec"] / aligned["bench"]
                rs_mean = rs.rolling(20).mean()
                rs_ratio = float(rs.iloc[-1] / rs_mean.iloc[-1] * 100) if float(rs_mean.iloc[-1]) > 0 else 100

                rs_mom_raw = float(rs.pct_change(5).iloc[-1])
                rs_momentum = rs_mom_raw * 100 + 100

                quad = compute_rrg_quadrant(rs_ratio, rs_momentum)

                week_chg = 0.0
                if len(aligned) >= 6:
                    week_chg = float(((aligned["sec"].iloc[-1] - aligned["sec"].iloc[-6]) /
                                      aligned["sec"].iloc[-6]) * 100)

                results[name] = {
                    "rs_ratio": round(rs_ratio, 2),
                    "rs_momentum": round(rs_momentum, 2),
                    "quadrant": quad,
                    "week_change": round(week_chg, 2),
                    "ticker": ticker,
                }
                log.debug("RRG %s: ratio=%.1f mom=%.1f", name, rs_ratio, rs_momentum)
            except Exception as exc:
                log.debug("RRG %s failed: %s", name, exc)

    except Exception as exc:
        log.warning("Benchmark download failed: %s", exc)
        _rrg_running = False
        return

    with _rrg_lock:
        sector_rotation_cache.clear()
        sector_rotation_cache.update(results)
        _rrg_built_at = time.time()

    _rrg_running = False
    log.info("RRG computed: %d sectors", len(results))


def get_sector_rotation_score(sector: str) -> tuple:
    """
    Returns (score, quadrant_string) for a given sector name.
    Looks up sector → Nifty index name → cached RRG data.
    """
    sector_l = sector.lower()
    matched_index = None
    # Match longest keyword first for specificity (e.g. 'banking' before 'bank')
    for key in sorted(SECTOR_TO_NIFTY.keys(), key=len, reverse=True):
        if key in sector_l:
            matched_index = SECTOR_TO_NIFTY[key]
            break

    if not matched_index:
        return 0, "UNKNOWN"  # No bad fit — better to leave unranked

    with _rrg_lock:
        data = sector_rotation_cache.get(matched_index, {})

    if not data:
        return 0, "UNKNOWN"

    quad = data.get("quadrant", "UNKNOWN")
    if "LEADING" in quad:
        score = 15
    elif "IMPROVING" in quad:
        score = 8
    elif "WEAKENING" in quad:
        score = -5
    else:  # LAGGING
        score = -10

    return score, quad


def get_rrg_data() -> dict:
    """Return full RRG cache for API endpoint."""
    with _rrg_lock:
        return dict(sector_rotation_cache)
