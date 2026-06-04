"""Centralized configuration constants."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Auth — Google OAuth
# Admins live in auth.db (users.is_admin); seed initial admins with
#   python scripts/add_admin.py <email>
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

# fingerprint.com Pro (device binding). Public key is embedded in HTML; secret
# key is used server-side to verify the visitorId via the Server API.
# Without the secret key the system falls back to trusting the client (OSS-equivalent).
FINGERPRINT_PUBLIC_KEY = os.environ.get("FINGERPRINT_PUBLIC_KEY", "")
FINGERPRINT_SECRET_KEY = os.environ.get("FINGERPRINT_SECRET_KEY", "")
FINGERPRINT_API_REGION = os.environ.get("FINGERPRINT_API_REGION", "us").lower()  # us|eu|ap

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
TOP_N_RESULTS = 750

# Batch scan settings — more conservative on Railway (shared IP gets rate-limited faster)
BATCH_SIZE = 30 if _ON_RAILWAY else 80
BATCH_DELAY = 20 if _ON_RAILWAY else 10  # seconds between batches

# Auto scan interval (minutes)
AUTO_SCAN_INTERVAL = 60

# ATR-based risk management
ATR_SL_MULTIPLIER = 2.0
TARGET_USES_RESISTANCE = True

# High Conviction thresholds (tighter in bear market)
HC_MIN_SCORE = 45
HC_MIN_SIGNALS_BULLISH = 5
HC_RSI_RANGE = (28, 70)
HC_DELIVERY_MIN = 45
HC_ATR_RANGE = (1.5, 5.5)
HC_RISK_MAX = 60
HC_REQUIRE_MACD_BULLISH = True
HC_REQUIRE_VOLUME = 1.0       # min volume ratio
HC_MIN_RISK_REWARD = 2.0

# Bear Play thresholds (oversold bounce in bear market)
BP_RSI_MAX = 40
BP_VOLUME_MIN = 1.2
BP_DELIVERY_MIN = 45
BP_WEEK1_MAX_LOSS = -2.0      # 1W return not worse than -2%
BP_MACD_BULLISH = True         # MACD must be bullish
BP_TARGET_PCT = 10.0           # realistic target in bear market
