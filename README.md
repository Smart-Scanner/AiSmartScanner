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
# Required
ANGEL_API_KEY=your_angel_one_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PASSWORD=your_password
ANGEL_TOTP_KEY=your_totp_secret

# Database (pick one)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your_supabase_key
# OR use SQLite (default, no config needed)

# Optional
MARKETAUX_API_KEY=your_marketaux_key
GOOGLE_CLIENT_ID=your_google_oauth_id
GOOGLE_CLIENT_SECRET=your_google_oauth_secret
SECRET_KEY=your_flask_secret
LOCAL_USERNAME=admin
LOCAL_PASSWORD=admin123
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

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/results` | Get all scanned stock results |
| GET | `/api/status` | Scan progress + system status |
| POST | `/api/scan` | Trigger a new scan |
| POST | `/api/force-scan` | Force refresh (bypass cache) |
| GET | `/api/export/csv` | Export results as CSV |
| GET | `/api/custom-stocks` | Get user's custom stock list |
| POST | `/api/custom-stocks` | Add a custom stock |
| DELETE | `/api/custom-stocks/<symbol>` | Remove a custom stock |
| GET | `/api/live-prices` | Get live prices for displayed stocks |
| GET | `/api/macro` | Market regime + macro indicators |
| GET | `/api/news` | Latest market news and alerts |

## Deployment

### Railway (Production)

```bash
# Requirements (no desktop dependencies)
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

## Project Structure

```
smart-screener/
├── app.py                  # Flask application entry
├── scanner.py              # Stock scanning engine
├── analyzer.py             # Technical analysis
├── fundamentals.py         # Fundamental analysis
├── news_engine.py          # News sentiment pipeline
├── db.py                   # Database abstraction
├── routes/
│   ├── api.py              # REST API endpoints
│   ├── pages.py            # Page routes
│   ├── auth.py             # Authentication
│   └── portfolio.py        # Portfolio management
├── templates/
│   ├── index.html          # V6 Dashboard (Command Center)
│   ├── local_login.html    # Standalone login page
│   ├── _public_base.html   # Public pages base
│   └── ...
├── static/                 # CSS, JS, images
├── cache/                  # File-based cache
├── start_screener.bat      # Windows control panel
└── requirements.txt        # Python dependencies
```

## License

Proprietary — All rights reserved.
