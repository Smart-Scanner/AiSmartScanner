"""Centralized configuration constants."""

import os
from pathlib import Path

# Phase 0: Scan version — increment on ANY scoring formula change
SCAN_VERSION = "v3.0.0"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Auth — Session Secret Key
# ---------------------------------------------------------------------------
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

# Paths
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_FILE = CACHE_DIR / "scan_results.json"

# Scan settings
CACHE_TTL_HOURS = 6
DATA_LOOKBACK_DAYS = 365
BENCHMARK_LOOKBACK_DAYS = 60
_ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT"))

MAX_WORKERS = 3 if _ON_RAILWAY else 4   # 4 parallel workers (intelligence adds per-stock overhead)
MAX_RAW_SCORE = 380   # 25 technical indicators (~220 pts) + 12 intelligence layers (~160 pts)
TOP_N_RESULTS = 3000
# Phase D: Dashboard endpoints use a smaller limit for faster queries.
# TOP_N_RESULTS remains unchanged at 3000 for export/admin endpoints.
DASHBOARD_MAX_RESULTS = int(os.getenv("DASHBOARD_MAX_RESULTS", "500"))

# Batch scan settings — more conservative on Railway (shared IP gets rate-limited faster)
BATCH_SIZE = 30 if _ON_RAILWAY else 80
BATCH_DELAY = 20 if _ON_RAILWAY else 10  # seconds between batches

# Phase 5: Batch processing + deep scan config
WRITE_BATCH_SIZE = int(os.getenv("WRITE_BATCH_SIZE", "50"))
FAST_SCAN_WORKERS = int(os.getenv("FAST_SCAN_WORKERS", "1"))
DEEP_SCAN_WORKERS = int(os.getenv("DEEP_SCAN_WORKERS", "3"))
DEEP_SCAN_MAX_CANDIDATES = int(os.getenv("DEEP_SCAN_MAX_CANDIDATES", "100"))

# Auto scan interval (minutes)
AUTO_SCAN_INTERVAL = int(os.getenv("AUTO_SCAN_INTERVAL", "60"))

# ATR-based risk management
ATR_SL_MULTIPLIER = 2.0
TARGET_USES_RESISTANCE = True

# High Conviction thresholds — Release 1 Calibration (P0.8 Audit-Derived)
# HC_MIN_SCORE = 55 is a Release 1 observation value subject to P1 Truth Audit.
HC_MIN_SCORE = 55            # was 50 — calibrated from P0.8 counterfactual ladder
HC_MIN_SIGNALS_BULLISH = 5
HC_RSI_RANGE = (40, 70)      # was (28, 70) — avoid deep oversold entries
HC_DELIVERY_MIN = 40         # was 45 — relaxed per audit funnel analysis
HC_ATR_RANGE = (1.5, 5.5)
HC_RISK_MAX = 40             # was 45 — tighter risk for quality
HC_REQUIRE_MACD_BULLISH = False  # was True — disabled to break Intersection of Death
HC_REQUIRE_VOLUME = 1.0       # min volume ratio — unchanged
HC_MIN_RISK_REWARD = 2.2     # unchanged

# Bear Play thresholds (oversold bounce in bear market)
BP_RSI_MAX = 40
BP_VOLUME_MIN = 1.2
BP_DELIVERY_MIN = 45
BP_WEEK1_MAX_LOSS = -2.0      # 1W return not worse than -2%
BP_MACD_BULLISH = True         # MACD must be bullish
BP_TARGET_PCT = 10.0           # realistic target in bear market
