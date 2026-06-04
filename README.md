# 🤖 Nifty 250 Short-Term Screener

**Find the best Nifty 250 stocks for short-term 10%+ returns in 2-4 weeks.**

Built by G One for R One.

## Features

- Scans all 250 Nifty stocks in real-time using Yahoo Finance data
- **10 Technical Indicators** scored for short-term momentum:
  - RSI (oversold bounce detection)
  - MACD (crossover & histogram)
  - EMA 9/21 crossover
  - Bollinger Bands squeeze
  - Volume surge detection
  - ATR (volatility sweet spot)
  - Stochastic Oscillator
  - Price momentum (1W, 2W, 1M)
  - OBV (smart money flow)
  - 52-week pullback analysis
- Score-ranked results with target price & stop loss
- Filters: High Score, Oversold, Volume Surge, Reversal
- Search by stock name, symbol, or sector
- Dark mode UI with responsive design

## Setup

```bash
pip install -r requirements.txt
python3 app.py
```

Open http://localhost:5000 and click **Start Scan**.

## Scoring

Each stock gets a score out of ~130 based on:
- **70+** = 🔥 Strong pick
- **45-69** = ⚡ Moderate potential
- **<45** = 📊 Weak signals

## Disclaimer

⚠️ This is for **educational purposes only**. Not financial advice. DYOR.
