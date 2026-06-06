# AI Smart Screener v6

**AI Smart Scanner** — Professional-grade stock screening with 18-factor AI scoring, live market intelligence, and risk-first analysis.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    AI Smart Screener                      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   Fast Scan (Zero yfinance in critical path)             │
│   ├── NSE Scraper (jugaad_data)                          │
│   ├── Angel One API (real-time quotes)                   │
│   └── Cache Layer (detail/financials/fundamentals)       │
│         ↓                                                │
│   Deep Scan (Per-stock intelligence)                     │
│   ├── Technical Analysis (RSI, ADX, MACD, trends)        │
│   ├── Fundamental Analysis (PE, PB, ROCE, debt)          │
│   ├── News Sentiment (FinBERT / MarketAux)               │
│   └── Sector Rotation (RRG quadrants)                    │
│         ↓                                                │
│   MarketAux Queue (Async news pipeline)                  │
│   ├── Rate-limited API calls                             │
│   ├── DLQ for failed requests                            │
│   └── Circuit breaker pattern                            │
│         ↓                                                │
│   Supabase / SQLite (Persistent storage)                 │
│   ├── Stock results                                      │
│   ├── User accounts & auth                               │
│   ├── Portfolio positions                                │
│   └── Watchlists                                         │
│         ↓                                                │
│   Flask Web UI (V6 Command Center)                       │
│   ├── Opportunity Deck (Top 5 picks)                     │
│   ├── Stock Table (2000+ stocks, incremental render)     │
│   ├── Detail Drawer ("Why This Stock?" explainability)   │
│   ├── Market Pulse (collapsed macro intelligence)        │
│   └── Portfolio Manager                                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Features

| Feature | Description |
|---------|-------------|
| **18-Factor Scoring** | Technical (RSI, ADX, MACD, trends) + Fundamental (PE, PB, ROCE) + Sentiment (news, volume) + Macro |
| **Live Prices** | Real-time LTP via Angel One API with auto-refresh during market hours |
| **News Sentiment** | FinBERT NLP scoring + MarketAux news pipeline with DLQ |
| **Market Regime** | Automatic bull/bear/sideways detection based on Nifty trend + VIX |
| **Portfolio Manager** | Track positions, P&L, allocation, and risk exposure |
| **Deep Scan Intel** | Entry zones, structural stop-losses, multi-target exits, R:R ratios |
| **Sector Rotation** | RRG-style quadrant analysis (Leading/Improving/Weakening/Lagging) |
| **Macro Analysis** | India VIX, US 10Y, DXY, Gold, Crude — sector impact indicators |
| **Explainability** | "Why This Stock?" — auto-generated top 3 reasons per stock |
| **Opportunity Deck** | Top 5 picks with conviction stars, catalyst tags, and target % |

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11+ / Flask |
| Database | Supabase (PostgreSQL) or SQLite |
| Market Data | Angel One SmartAPI, jugaad_data, NSE scraping |
| News | MarketAux API, FinBERT sentiment |
| Auth | Local auth + Google OAuth 2.0 |
| Frontend | Jinja2 templates, vanilla JS, Inter/Outfit fonts |
| Caching | File-based cache (detail/financials/fundamentals) |
| Resilience | Circuit breakers, DLQ, exponential backoff |

## Setup

### 1. Environment Variables

```bash
# Angel One SmartAPI (required for market data)
ANGEL_API_KEY=your_angel_one_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_MPIN=your_mpin
ANGEL_TOTP_SECRET=your_totp_secret

# Database — PostgreSQL connection string (Supabase Transaction Pooler recommended)
# Use port 6543 (transaction pooler), NOT 5432 (session pooler)
# If not set, falls back to local SQLite automatically
DATABASE_URL=postgresql://user:pass@db.xxx.supabase.co:6543/postgres

# Flask session signing
FLASK_SECRET_KEY=your_flask_secret_key

# Google OAuth (required for login)
GOOGLE_CLIENT_ID=your_google_oauth_id
GOOGLE_CLIENT_SECRET=your_google_oauth_secret

# Optional
MARKETAUX_API_KEY=your_marketaux_key
```

### 2. Install & Run

```bash
# Clone
git clone https://github.com/your-repo/smart-screener.git
cd smart-screener

# Install dependencies
pip install -r requirements.txt

# Run
python app.py
# or use the control panel:
start_screener.bat
```

### 3. Control Panel (Windows)

Double-click `SmartScreener.lnk` or run `start_screener.bat`:

```
╔═══════════════════════════════════════╗
║   AI Smart Screener — Control Panel   ║
╠═══════════════════════════════════════╣
║  1. Start Server                      ║
║  2. Stop Server                       ║
║  3. Clear All Caches                  ║
║  4. Health Check                      ║
║  5. Exit                              ║
╚═══════════════════════════════════════╝
```

## API Endpoints

### Core

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dashboard` | Composite endpoint — status + results + sectors + paper stats |
| GET | `/api/results` | All scanned stock results (slimmed, ~873 KB) |
| GET | `/api/status` | Scan progress + HC/golden/adv/dec counts |
| GET | `/api/search-list` | Lightweight symbol list for autocomplete (~71 KB) |
| GET | `/api/stock/<symbol>` | Full stock detail for drawer (all fields) |
| GET | `/api/health` | System health + pool metrics |

### Filtered Lists

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/golden` | Golden stocks only |
| GET | `/api/high-conviction` | High conviction stocks only |
| GET | `/api/breakouts` | Breakout candidates |
| POST | `/api/watchlist/details` | Watchlist stock details (POST with symbols array) |

### Paper Trading & Outcome Intelligence

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/paper-trades` | All paper trades (open + closed) |
| GET | `/api/paper-trades/stats` | Win rate, expectancy, model comparison |
| GET | `/api/paper-trades/equity-curve` | Equity curve chart data |

### Scanner & Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scan` | Trigger a new scan |
| POST | `/api/force-scan` | Force refresh (bypass cache) |
| GET | `/api/export/csv` | Export results as CSV |
| GET | `/api/custom-stocks` | User's custom stock list |
| POST | `/api/custom-stocks` | Add a custom stock |
| DELETE | `/api/custom-stocks/<symbol>` | Remove a custom stock |
| GET | `/api/macro` | Market regime + macro indicators |
| GET | `/api/news` | Latest market news and alerts |

## Deployment

### Requirements

| Environment | File | Includes |
|-------------|------|----------|
| **Railway / Production** | `requirements.txt` | Core + Flask + gunicorn + torch |
| **Desktop (local)** | `requirements-desktop.txt` | Everything above + `pywebview` |

### Railway (Production)

```bash
pip install -r requirements.txt

# Procfile (auto-detected by Railway)
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

Required environment variables on Railway:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Supabase PostgreSQL connection string |
| `ANGEL_API_KEY` | Angel One SmartAPI key |
| `ANGEL_CLIENT_ID` | Angel One client ID |
| `ANGEL_MPIN` | Angel One MPIN |
| `ANGEL_TOTP_SECRET` | Angel One TOTP secret |
| `FLASK_SECRET_KEY` | Flask session signing key |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |

#### Supabase Connection

Use **Transaction Pooler** (port `6543`), not Session Pooler (port `5432`).
Session mode has a 15-connection hard limit that causes `EMAXCONNSESSION` errors.

```
# Recommended
postgresql://user:pass@host:6543/postgres

# Avoid
postgresql://user:pass@host:5432/postgres
```

### Desktop Mode (Local)

```bash
# Install with desktop GUI dependencies
pip install -r requirements-desktop.txt

# Launch native desktop window
python run_desktop.py

# Or run as web server only
python app.py
```

### Other Cloud Providers

See [DEPLOY.md](DEPLOY.md) for detailed deployment instructions.

## Performance

Payload sizes after V6 optimization (75% reduction from V5):

| Endpoint | Size | Cache TTL |
|----------|------|-----------|
| `/api/status` | ~1 KB | 5s |
| `/api/search-list` | ~71 KB | 60s |
| `/api/dashboard` | ~870 KB | 10s |
| `/api/results` | ~873 KB | 10s |
| `/api/golden` | ~0-50 KB | — |
| `/api/breakouts` | ~22 KB | — |
| `/api/watchlist/details` | ~6 KB | — |

Heavy fields (`chart_data`, `signals`, `fundamentals`, `trade`) are stripped from list endpoints and loaded on-demand via `/api/stock/<symbol>` in the detail drawer.

## Project Structure

```
smart-screener/
├── app.py                  # Flask entry + background threads
├── scanner.py              # Stock scanning engine (Phase 1 + 2)
├── analyzer.py             # 18-factor technical analysis
├── db.py                   # Supabase + SQLite dual-backend
├── live_feed.py            # Angel One WebSocket live prices
├── cache_layer.py          # TTL memory cache for API responses
├── config.py               # Centralized configuration
├── intelligence/            # FinBERT, GDELT, RRG, macro layers
├── routes/
│   ├── api.py              # REST API endpoints
│   ├── pages.py            # V3 page routes
│   ├── auth.py             # Google OAuth + local auth
│   ├── admin.py            # Admin endpoints
│   └── portfolio.py        # Portfolio management
├── templates/
│   ├── _app_base.html      # V3 base layout + sidebar
│   ├── v3/                 # V3 pages (top_picks, golden, etc.)
│   └── index.html          # Legacy V2 (backward compat)
├── static/                 # CSS, JS, images
├── requirements.txt        # Production dependencies
├── requirements-desktop.txt # Desktop mode (+ pywebview)
├── Procfile                # Railway/Heroku deployment
└── start_screener.bat      # Windows control panel
```

## License

Proprietary — All rights reserved.
