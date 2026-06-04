# 👑 Advanced Quant-Based AI Smart Scanner (NSE Universe)

An ultra-premium, production-grade quantitative trading scanner built to scan the entire NSE universe (2,266+ instruments) to find underpriced and high-conviction swing trading opportunities. It utilizes a 12-layer multi-dimensional intelligence engine backed by a Supabase PostgreSQL database, FinBERT news sentiment analysis, and a live 30-minute portfolio position scanner.

---

## 🌟 Architecture & The 12-Layer Intelligence Engine

The scanner aggregates data from multiple free/low-cost sources and feeds them into 12 layers of analytical intelligence:

1. **Layer 1: 25+ Technical Indicators** - RSI, MACD, EMA stack (9/21/50/200), Bollinger Bands squeeze, ATR, OBV, VWAP, ADX, CCI, Stochastic, Pivot points S/R, Fibonacci retracement levels.
2. **Layer 2: Multi-Timeframe Alignment** - Synced trends across 1-Day, 1-Week, and 1-Month timeframes.
3. **Layer 3: Support & Resistance Zones** - Identification of key institutional buying/selling zones.
4. **Layer 4: Fundamental Quality** - Analysis of P/E, P/B, ROE, Debt-to-Equity, Promoter holding percentage, and Free Cash Flow.
5. **Layer 5: Seasonality Engine** - Analyzes historical monthly win rate patterns for sectors in the Indian market.
6. **Layer 6: Order Book Proxy** - Analyzes block delivery percentages and institutional accumulation signatures.
7. **Layer 7: Sector Rotation (RRG)** - Relative Rotation Graphs (RRG) classifying sectors into Leading, Weakening, Lagging, or Improving quadrants.
8. **Layer 8: GDELT + FinBERT News Sentiment** - Real-time global news scanning with FinBERT neural network sentiment scoring.
9. **Layer 9: News Sentiment Waterfall** - Cross-checks sentiment volume spikes and integrates MarketAux for catalyst detection.
10. **Layer 10: Macro Economic Indexes** - Correlates global market states (Nasdaq, Dow Jones, VIX, India VIX).
11. **Layer 11: FRED Macro Integration** - Feeds US Fed Rates, CPI inflation data, US 10-Year yields, and Dollar Index (DXY) to evaluate risk-on/risk-off states.
12. **Layer 12: Corporate Action Events** - Integrates earnings calendars and corporate announcements.

---

## 👑 Golden Stocks (High Conviction Rule)

Stocks are classified as **Golden Stocks** if they satisfy the following joint mathematical criteria across all layers:
- **Composite Score**: `Score >= 80` (out of 100)
- **Technical Score**: `Technical Score >= 18.0` (out of 25)
- **News Sentiment**: `News Sentiment >= 20.0` (out of 30)
- **Fundamental Quality**: `Fundamental Score >= 10.0` (out of 15)
- **Risk-to-Reward Ratio**: `R:R >= 2.2`
- **Risk Level**: `Risk Score <= 45`

*Any Golden Stock is automatically classified as a High Conviction candidate and represents a premium trade opportunity.*

---

## 📊 Features & UI Capabilities

- **Complete NSE Universe Scan**: Scans all 2,266+ equities listed on NSE.
- **Supabase Cloud Database**: Stores scan results, historical score tracking, and user portfolios in a hosted PostgreSQL instance.
- **30-Min Live Portfolio Check**: Continuously scans open positions in the background, recommending Hold/Trail/Book Profit/Exit strategies based on real-time prices.
- **Dynamic Candidate Tabs**:
  - **All Scanned**: View all scanned instruments.
  - **👑 Golden Stocks**: The intersection of the highest probability parameters.
  - **Top 20 Swing**: Momentum breakout setups.
  - **Top 20 News-Based**: High GDELT/FinBERT sentiment score candidates.
  - **Top 20 Breakouts**: High volume Bollinger Band squeeze breakouts.
  - **Top 20 Underdogs**: High potential small-cap and micro-cap opportunities.
- **Real-Time News & Alerts Feed**: Displays live breakout alerts, volume spikes, and news sentiment articles directly on the dashboard.
- **Responsive Scrollability**: Custom media queries allowing natural vertical scroll on laptop screens and displays below 1080p.

---

## ⚙️ Setup & Deployment

### 1. Requirements
Ensure your Python environment has the dependencies installed:
```bash
pip install -r requirements.txt
```

### 2. Environment Variables (`.env`)
Configure the credentials and Supabase database connection string in a local `.env` file:
```ini
ANGEL_API_KEY=your_angel_key
ANGEL_SECRET_KEY=your_angel_secret
ANGEL_CLIENT_ID=your_client_id
ANGEL_MPIN=your_mpin
ANGEL_TOTP_SECRET=your_totp_secret

# Supabase PostgreSQL Connection String (Session Pooler)
DATABASE_URL=postgresql://postgres.tithybqsriohuzpatmfa:kGL%25b6Gx67aH2Lx@aws-1-ap-south-1.pooler.supabase.com:5432/postgres

# Google OAuth (for secure user auth)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
FLASK_SECRET_KEY=your_flask_secret_key

# 12-Layer Intelligence Keys
FRED_API_KEY=your_fred_key
MARKETAUX_API_KEY=your_marketaux_key
NEWS_API_KEY=your_newsapi_key
```

### 3. Run Locally
To start the Flask development server:
```bash
python app.py
```
Open [http://localhost:5050](http://localhost:5050) in your browser.

---

## 🛠️ Verification & Testing
To confirm the database setup, run:
```bash
python db.py
```
This will test the connection to your Supabase PostgreSQL cluster and initialize all required schemas.

---

## ⚠️ Disclaimer
This scanner is for **educational and research purposes only**. Trading equities involves substantial risk. Perform your own due diligence before executing any trades.
