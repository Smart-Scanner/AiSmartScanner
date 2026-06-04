"""
SQLite and PostgreSQL database wrapper for Smart Screener.
Stores scan results, historical scores, metadata, and normalized analytics tables.
"""

import os
import json
import logging
import threading
import sqlite3
from pathlib import Path
from datetime import datetime

log = logging.getLogger("db")

DB_PATH = Path(__file__).parent / "cache" / "screener.db"
_local = threading.local()

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

def is_postgresql() -> bool:
    """Check if the app is configured to use a PostgreSQL database."""
    return bool(DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")))

def _get_conn():
    """Get a thread-local database connection (PostgreSQL or SQLite)."""
    pg = is_postgresql()
    if pg:
        if not hasattr(_local, "pg_conn") or _local.pg_conn is None or _local.pg_conn.closed:
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                url = DATABASE_URL
                if url.startswith("postgres://"):
                    url = url.replace("postgres://", "postgresql://", 1)
                # Ensure SSL mode is enabled for Supabase
                if "sslmode" not in url:
                    url += "&sslmode=require" if "?" in url else "?sslmode=require"
                _local.pg_conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
                _local.pg_conn.autocommit = True
            except Exception as exc:
                log.error("Failed to connect to PostgreSQL/Supabase: %s. Falling back to SQLite.", exc)
                # Set url to None to trigger SQLite fallback
                return _get_sqlite_conn()
        return _local.pg_conn
    else:
        return _get_sqlite_conn()

def _get_sqlite_conn() -> sqlite3.Connection:
    """Get thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH), timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn

def _to_native(val):
    if hasattr(val, "item"):
        try:
            return val.item()
        except Exception:
            pass
    if isinstance(val, list):
        return [_to_native(v) for v in val]
    if isinstance(val, tuple):
        return tuple(_to_native(v) for v in val)
    if isinstance(val, dict):
        return {k: _to_native(v) for k, v in val.items()}
    return val

def execute_db(query: str, params: tuple = None, fetch: str = None):
    """
    Unified query executor for PostgreSQL and SQLite.
    Automatically translates '?' placeholders to '%s' for PG.
    """
    if params is not None:
        params = tuple(_to_native(v) for v in params)
        
    pg = is_postgresql()
    conn = _get_conn()
    
    # Check if we fell back to SQLite even though PG was requested
    if pg and not hasattr(_local, "pg_conn"):
        pg = False
        
    if pg:
        query_pg = query.replace("?", "%s")
        cur = conn.cursor()
        try:
            cur.execute(query_pg, params or ())
            if fetch == "one":
                res = cur.fetchone()
                return res
            elif fetch == "all":
                return cur.fetchall()
            elif fetch == "count":
                res = cur.fetchone()
                return list(res.values())[0] if res else 0
            return None
        except Exception as exc:
            log.error("PostgreSQL Query error: %s | Query: %s | Params: %s", exc, query_pg, params)
            raise
    else:
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            if fetch == "one":
                res = cursor.fetchone()
                return dict(res) if res else None
            elif fetch == "all":
                return [dict(r) for r in cursor.fetchall()]
            elif fetch == "count":
                res = cursor.fetchone()
                return res[0] if res else 0
            conn.commit()
            return None
        except Exception as exc:
            log.error("SQLite Query error: %s | Query: %s | Params: %s", exc, query, params)
            raise

def init_db():
    """Create tables if they don't exist."""
    pg = is_postgresql()
    conn = _get_conn()
    if pg and hasattr(_local, "pg_conn"):
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scan_results (
                symbol TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                score INTEGER DEFAULT 0,
                high_conviction INTEGER DEFAULT 0,
                sector TEXT DEFAULT '',
                scan_date TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scan_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS score_history (
                symbol TEXT NOT NULL,
                score INTEGER NOT NULL,
                price REAL NOT NULL,
                rsi REAL,
                scan_date TEXT NOT NULL,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS custom_stocks (
                symbol TEXT PRIMARY KEY,
                exchange TEXT DEFAULT 'NSE',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS portfolios (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS positions (
                id SERIAL PRIMARY KEY,
                portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                trade_type TEXT DEFAULT 'BUY',
                quantity INTEGER NOT NULL DEFAULT 1,
                buy_price REAL NOT NULL,
                buy_date TEXT NOT NULL,
                sell_price REAL,
                sell_date TEXT,
                stop_loss REAL,
                target REAL,
                status TEXT DEFAULT 'OPEN',
                notes TEXT DEFAULT '',
                scan_analysis TEXT DEFAULT 'Hold (Position Active)',
                last_scan_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Normalized scanner tables
            CREATE TABLE IF NOT EXISTS stocks (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                sector TEXT,
                industry TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS news_articles (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT,
                age_hours REAL,
                raw_score REAL,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sentiment_scores (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                gdelt_sentiment REAL,
                gdelt_spike REAL,
                gdelt_freshness REAL,
                final_sentiment_score REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS technical_indicators (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                rsi REAL,
                adx REAL,
                macd_signal TEXT,
                volume_ratio REAL,
                atr_pct REAL,
                stoch_k REAL,
                stoch_d REAL,
                pct_1w REAL,
                pct_2w REAL,
                pct_1m REAL,
                bb_position REAL,
                dist_from_high REAL,
                rs_vs_nifty REAL,
                vwap_position REAL,
                is_breakout BOOLEAN,
                vp_divergence BOOLEAN,
                weekly_trend TEXT,
                below_ema200 BOOLEAN,
                high_52w REAL,
                low_52w REAL,
                pullback_pct REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT PRIMARY KEY,
                pe REAL,
                pb REAL,
                fwd_pe REAL,
                roe REAL,
                roa REAL,
                revenue_growth REAL,
                earnings_growth REAL,
                debt_to_equity REAL,
                promoter_pct REAL,
                market_cap REAL,
                free_cash_flow REAL,
                total_revenue REAL,
                capex REAL,
                eps_fwd REAL,
                eps_trail REAL,
                fund_score INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS macro_events (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                country TEXT,
                impact TEXT,
                actual TEXT,
                forecast TEXT,
                surprise_dir TEXT,
                score REAL,
                event_date TEXT,
                event_time TEXT,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS final_scores (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                news_sentiment_score REAL,
                news_spike_score REAL,
                technical_score REAL,
                fundamental_score REAL,
                macro_score REAL,
                marketaux_score REAL,
                final_score REAL,
                grade TEXT,
                high_conviction BOOLEAN,
                bear_play BOOLEAN,
                is_golden BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, scan_date)
            );
        """)
        log.info("PostgreSQL tables checked/created.")
    else:
        conn = _get_sqlite_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_results (
                symbol TEXT PRIMARY KEY,
                data JSON NOT NULL,
                score INTEGER DEFAULT 0,
                high_conviction INTEGER DEFAULT 0,
                sector TEXT DEFAULT '',
                scan_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS score_history (
                symbol TEXT NOT NULL,
                score INTEGER NOT NULL,
                price REAL NOT NULL,
                rsi REAL,
                scan_date TEXT NOT NULL,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS custom_stocks (
                symbol TEXT PRIMARY KEY,
                exchange TEXT DEFAULT 'NSE',
                added_at TEXT NOT NULL,
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                trade_type TEXT DEFAULT 'BUY',
                quantity INTEGER NOT NULL DEFAULT 1,
                buy_price REAL NOT NULL,
                buy_date TEXT NOT NULL,
                sell_price REAL,
                sell_date TEXT,
                stop_loss REAL,
                target REAL,
                status TEXT DEFAULT 'OPEN',
                notes TEXT DEFAULT '',
                scan_analysis TEXT DEFAULT 'Hold (Position Active)',
                last_scan_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
            );

            -- Normalized scanner tables
            CREATE TABLE IF NOT EXISTS stocks (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                sector TEXT,
                industry TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT,
                age_hours REAL,
                raw_score REAL,
                scanned_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sentiment_scores (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                gdelt_sentiment REAL,
                gdelt_spike REAL,
                gdelt_freshness REAL,
                final_sentiment_score REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS technical_indicators (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                rsi REAL,
                adx REAL,
                macd_signal TEXT,
                volume_ratio REAL,
                atr_pct REAL,
                stoch_k REAL,
                stoch_d REAL,
                pct_1w REAL,
                pct_2w REAL,
                pct_1m REAL,
                bb_position REAL,
                dist_from_high REAL,
                rs_vs_nifty REAL,
                vwap_position REAL,
                is_breakout INTEGER,
                vp_divergence INTEGER,
                weekly_trend TEXT,
                below_ema200 INTEGER,
                high_52w REAL,
                low_52w REAL,
                pullback_pct REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, scan_date)
            );

            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT PRIMARY KEY,
                pe REAL,
                pb REAL,
                fwd_pe REAL,
                roe REAL,
                roa REAL,
                revenue_growth REAL,
                earnings_growth REAL,
                debt_to_equity REAL,
                promoter_pct REAL,
                market_cap REAL,
                free_cash_flow REAL,
                total_revenue REAL,
                capex REAL,
                eps_fwd REAL,
                eps_trail REAL,
                fund_score INTEGER,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS macro_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                country TEXT,
                impact TEXT,
                actual TEXT,
                forecast TEXT,
                surprise_dir TEXT,
                score REAL,
                event_date TEXT,
                event_time TEXT,
                scanned_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS final_scores (
                symbol TEXT NOT NULL,
                scan_date TEXT NOT NULL,
                news_sentiment_score REAL,
                news_spike_score REAL,
                technical_score REAL,
                fundamental_score REAL,
                macro_score REAL,
                marketaux_score REAL,
                final_score REAL,
                grade TEXT,
                high_conviction INTEGER,
                bear_play INTEGER,
                is_golden INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, scan_date)
            );

            DROP TABLE IF EXISTS users;
        """)
        log.info("SQLite Database initialized: %s", DB_PATH)

# ─── Scan Results ───

def save_results(results: list[dict], meta: dict = None):
    """Save scan results to DB and populate normalized tables."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scan_date = datetime.now().strftime("%Y-%m-%d")

    for r in results:
        sym = r["symbol"]
        
        # 1. Main scan results table (JSON)
        execute_db("""
            INSERT INTO scan_results (symbol, data, score, high_conviction, sector, scan_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                data=excluded.data, score=excluded.score,
                high_conviction=excluded.high_conviction, sector=excluded.sector,
                scan_date=excluded.scan_date, updated_at=excluded.updated_at
        """, (sym, json.dumps(r), r.get("score", 0), 1 if r.get("high_conviction") else 0, r.get("sector", ""), scan_date, now))

        # 2. score_history
        execute_db("""
            INSERT INTO score_history (symbol, score, price, rsi, scan_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, scan_date) DO UPDATE SET
                score=excluded.score, price=excluded.price, rsi=excluded.rsi
        """, (sym, r.get("score", 0), r.get("price", 0.0), r.get("rsi"), scan_date))

        # 3. stocks
        f = r.get("fundamentals", {})
        execute_db("""
            INSERT INTO stocks (symbol, name, sector, industry, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name, sector=excluded.sector, industry=excluded.industry, updated_at=excluded.updated_at
        """, (sym, r.get("name", sym), r.get("sector", "Other"), f.get("industry", ""), now))

        # 4. news_articles
        # Clear existing ones first to keep it fresh
        execute_db("DELETE FROM news_articles WHERE symbol=?", (sym,))
        gdelt_data = r.get("gdelt", {})
        articles = gdelt_data.get("articles", [])
        # Also include MarketAux articles if available in news_sentiment items
        news_s = r.get("news_sentiment", {})
        for item in news_s.get("items", []):
            if item.get("source") == "marketaux":
                articles.append({
                    "title": item.get("title", ""),
                    "score": item.get("score", 0.0),
                    "source": "marketaux",
                    "age_h": 1.0
                })
        for art in articles[:10]:
            execute_db("""
                INSERT INTO news_articles (symbol, title, url, source, age_hours, raw_score, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sym, art.get("title", ""), art.get("url", ""), art.get("source", "GDELT"), art.get("age_h", 12.0), art.get("score", 0.0), now))

        # 5. sentiment_scores
        execute_db("""
            INSERT INTO sentiment_scores (symbol, scan_date, gdelt_sentiment, gdelt_spike, gdelt_freshness, final_sentiment_score, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, scan_date) DO UPDATE SET
                gdelt_sentiment=excluded.gdelt_sentiment, gdelt_spike=excluded.gdelt_spike,
                gdelt_freshness=excluded.gdelt_freshness, final_sentiment_score=excluded.final_sentiment_score,
                updated_at=excluded.updated_at
        """, (sym, scan_date, gdelt_data.get("sentiment", 0.0), gdelt_data.get("spike", 1.0), gdelt_data.get("freshness", 0.0), r.get("news_sentiment_score", 0.0), now))

        # 6. technical_indicators
        execute_db("""
            INSERT INTO technical_indicators (
                symbol, scan_date, rsi, adx, macd_signal, volume_ratio, atr_pct, stoch_k, stoch_d,
                pct_1w, pct_2w, pct_1m, bb_position, dist_from_high, rs_vs_nifty, vwap_position,
                is_breakout, vp_divergence, weekly_trend, below_ema200, high_52w, low_52w, pullback_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, scan_date) DO UPDATE SET
                rsi=excluded.rsi, adx=excluded.adx, macd_signal=excluded.macd_signal,
                volume_ratio=excluded.volume_ratio, atr_pct=excluded.atr_pct, stoch_k=excluded.stoch_k,
                stoch_d=excluded.stoch_d, pct_1w=excluded.pct_1w, pct_2w=excluded.pct_2w, pct_1m=excluded.pct_1m,
                bb_position=excluded.bb_position, dist_from_high=excluded.dist_from_high, rs_vs_nifty=excluded.rs_vs_nifty,
                vwap_position=excluded.vwap_position, is_breakout=excluded.is_breakout, vp_divergence=excluded.vp_divergence,
                weekly_trend=excluded.weekly_trend, below_ema200=excluded.below_ema200, high_52w=excluded.high_52w,
                low_52w=excluded.low_52w, pullback_pct=excluded.pullback_pct, updated_at=excluded.updated_at
        """, (
            sym, scan_date, r.get("rsi"), r.get("adx"), r.get("macd_signal"), r.get("volume_ratio"), r.get("atr_pct"),
            r.get("stoch_k"), r.get("stoch_d"), r.get("pct_1w"), r.get("pct_2w"), r.get("pct_1m"), r.get("bb_position"),
            r.get("dist_from_high"), r.get("rs_vs_nifty"), r.get("vwap_position"),
            1 if r.get("is_breakout") else 0, 1 if r.get("vp_divergence") else 0, r.get("weekly_trend", "flat"),
            1 if r.get("below_ema200") else 0, r.get("high_52w"), r.get("low_52w"), r.get("pullback_pct"), now
        ))

        # 7. fundamentals
        execute_db("""
            INSERT INTO fundamentals (
                symbol, pe, pb, fwd_pe, roe, roa, revenue_growth, earnings_growth,
                debt_to_equity, promoter_pct, market_cap, free_cash_flow, total_revenue, capex, eps_fwd, eps_trail, fund_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                pe=excluded.pe, pb=excluded.pb, fwd_pe=excluded.fwd_pe, roe=excluded.roe, roa=excluded.roa,
                revenue_growth=excluded.revenue_growth, earnings_growth=excluded.earnings_growth,
                debt_to_equity=excluded.debt_to_equity, promoter_pct=excluded.promoter_pct,
                market_cap=excluded.market_cap, free_cash_flow=excluded.free_cash_flow,
                total_revenue=excluded.total_revenue, capex=excluded.capex, eps_fwd=excluded.eps_fwd,
                eps_trail=excluded.eps_trail, fund_score=excluded.fund_score, updated_at=excluded.updated_at
        """, (
            sym, f.get("pe"), f.get("pb"), f.get("fwd_pe"), f.get("roe"), f.get("roa"), f.get("revenue_growth"),
            f.get("earnings_growth"), f.get("debt_to_equity"), f.get("promoter_pct"), f.get("market_cap"),
            f.get("free_cash_flow"), f.get("total_revenue"), f.get("capex"), f.get("eps_fwd"), f.get("eps_trail"),
            f.get("fund_score", 0), now
        ))

        # 8. final_scores
        execute_db("""
            INSERT INTO final_scores (
                symbol, scan_date, news_sentiment_score, news_spike_score, technical_score,
                fundamental_score, macro_score, marketaux_score, final_score, grade, high_conviction, bear_play, is_golden, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, scan_date) DO UPDATE SET
                news_sentiment_score=excluded.news_sentiment_score, news_spike_score=excluded.news_spike_score,
                technical_score=excluded.technical_score, fundamental_score=excluded.fundamental_score,
                macro_score=excluded.macro_score, marketaux_score=excluded.marketaux_score,
                final_score=excluded.final_score, grade=excluded.grade,
                high_conviction=excluded.high_conviction, bear_play=excluded.bear_play,
                is_golden=excluded.is_golden, updated_at=excluded.updated_at
        """, (
            sym, scan_date, r.get("news_sentiment_score", 0.0), r.get("news_spike_score", 0.0), r.get("technical_score", 0.0),
            r.get("fundamental_score", 0.0), r.get("macro_score", 0.0), r.get("marketaux_catalyst_score", 0.0),
            r.get("score", 0), r.get("grade", ""), 1 if r.get("high_conviction") else 0, 1 if r.get("bear_play") else 0,
            1 if r.get("is_golden") else 0, now
        ))

    if meta:
        for k, v in meta.items():
            set_meta(k, v)

    log.info("Saved %d results to DB", len(results))

def save_macro_events(events: list):
    """Save Forex Factory macro events into the macro_events table."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Clean old events
    execute_db("DELETE FROM macro_events")
    for ev in events:
        execute_db("""
            INSERT INTO macro_events (title, country, impact, actual, forecast, surprise_dir, score, event_date, event_time, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ev.get("title"), ev.get("country"), ev.get("impact"), str(ev.get("actual", "")), str(ev.get("forecast", "")), ev.get("surprise_dir"), ev.get("score", 0.0), ev.get("date"), ev.get("time"), now))
    log.info("Saved %d macro events to database", len(events))

def load_results(limit: int = 750) -> list[dict]:
    """Load scan results from DB, ordered by score."""
    rows = execute_db("SELECT data FROM scan_results ORDER BY score DESC LIMIT ?", (limit,), fetch="all")
    return [json.loads(row["data"]) for row in rows]

def get_result_count() -> int:
    return execute_db("SELECT COUNT(*) as cnt FROM scan_results", fetch="count")

def get_meta(key: str, default=None):
    """Get a metadata value."""
    row = execute_db("SELECT value FROM scan_meta WHERE key=?", (key,), fetch="one")
    if row:
        val = row["value"]
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError, ValueError):
            return val
    return default

def set_meta(key: str, value):
    """Set a metadata value."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    v = json.dumps(value) if not isinstance(value, str) else value
    execute_db("""
        INSERT INTO scan_meta (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (key, v, now))

def get_stock(symbol: str) -> dict | None:
    """Get a single stock's scan data."""
    row = execute_db("SELECT data FROM scan_results WHERE symbol=?", (symbol.upper(),), fetch="one")
    return json.loads(row["data"]) if row else None

def get_stocks_map(symbols: list[str]) -> dict[str, dict]:
    """Get multiple stocks by symbol in one query."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = execute_db(
        f"SELECT symbol, data FROM scan_results WHERE symbol IN ({placeholders})",
        [s.upper() for s in symbols],
        fetch="all"
    )
    return {row["symbol"]: json.loads(row["data"]) for row in rows}

def get_all_symbols() -> list[str]:
    """Get all symbols in scan_results."""
    rows = execute_db("SELECT symbol FROM scan_results ORDER BY score DESC", fetch="all")
    return [row["symbol"] for row in rows]

def get_score_history(symbol: str, days: int = 30) -> list[dict]:
    """Get score history for a stock."""
    rows = execute_db("""
        SELECT symbol, score, price, rsi, scan_date
        FROM score_history WHERE symbol=?
        ORDER BY scan_date DESC LIMIT ?
    """, (symbol.upper(), days), fetch="all")
    return rows

def get_sector_stats() -> list[dict]:
    """Get sector-wise stats."""
    rows = execute_db("""
        SELECT sector, COUNT(*) as count,
               AVG(score) as avg_score,
               SUM(high_conviction) as hc_count
        FROM scan_results
        GROUP BY sector
        ORDER BY avg_score DESC
    """, fetch="all")
    return rows

def clear_old_results(days: int = 7):
    """Remove results older than N days."""
    if is_postgresql() and not hasattr(_local, "conn"):
        # PG Timestamp comparison
        execute_db("DELETE FROM scan_results WHERE updated_at < NOW() - INTERVAL '? days'", (days,))
    else:
        execute_db("""
            DELETE FROM scan_results
            WHERE julianday('now') - julianday(updated_at) > ?
        """, (days,))

# ─── Custom Stocks ───

def add_custom_stock(symbol: str, exchange: str = "NSE", note: str = "") -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        execute_db("""
            INSERT INTO custom_stocks (symbol, exchange, added_at, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note=excluded.note
        """, (symbol.upper(), exchange.upper(), now, note))
        return True
    except Exception:
        return False

def remove_custom_stock(symbol: str) -> bool:
    pg = is_postgresql()
    # In sqlite/postgres, delete returns affected row count differently or same
    conn = _get_conn()
    cur = conn.cursor()
    if pg and not hasattr(_local, "conn"):
        cur.execute("DELETE FROM custom_stocks WHERE symbol=%s", (symbol.upper(),))
        rowcount = cur.rowcount
    else:
        cur.execute("DELETE FROM custom_stocks WHERE symbol=?", (symbol.upper(),))
        conn.commit()
        rowcount = cur.rowcount
    return rowcount > 0

def get_custom_stocks() -> list[dict]:
    rows = execute_db("SELECT symbol, exchange, added_at, note FROM custom_stocks ORDER BY added_at DESC", fetch="all")
    # Format added_at as string
    res = []
    for r in rows:
        item = dict(r)
        if isinstance(item.get("added_at"), datetime):
            item["added_at"] = item["added_at"].strftime("%Y-%m-%d %H:%M:%S")
        res.append(item)
    return res

def is_custom_stock(symbol: str) -> bool:
    row = execute_db("SELECT 1 FROM custom_stocks WHERE symbol=?", (symbol.upper(),), fetch="one")
    return row is not None

# ─── Portfolios ───

def create_portfolio(name: str, description: str = "") -> int:
    pg = is_postgresql()
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if pg and not hasattr(_local, "conn"):
        cur.execute("INSERT INTO portfolios (name, description, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING id", (name, description, now, now))
        row = cur.fetchone()
        return row["id"] if row else 0
    else:
        cur.execute("INSERT INTO portfolios (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)", (name, description, now, now))
        conn.commit()
        return cur.lastrowid

def get_portfolios() -> list[dict]:
    rows = execute_db("SELECT * FROM portfolios ORDER BY created_at DESC", fetch="all")
    return [dict(r) for r in rows]

def get_portfolio(pid: int) -> dict | None:
    row = execute_db("SELECT * FROM portfolios WHERE id=?", (pid,), fetch="one")
    return dict(row) if row else None

def update_portfolio(pid: int, name: str = None, description: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if name:
        execute_db("UPDATE portfolios SET name=?, updated_at=? WHERE id=?", (name, now, pid))
    if description is not None:
        execute_db("UPDATE portfolios SET description=?, updated_at=? WHERE id=?", (description, now, pid))

def delete_portfolio(pid: int):
    execute_db("DELETE FROM positions WHERE portfolio_id=?", (pid,))
    execute_db("DELETE FROM portfolios WHERE id=?", (pid,))

# ─── Positions (Trades) ───

def add_position(portfolio_id: int, symbol: str, quantity: int, buy_price: float,
                 buy_date: str, stop_loss: float = None, target: float = None,
                 notes: str = "") -> int:
    pg = is_postgresql()
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if pg and not hasattr(_local, "conn"):
        cur.execute("""
            INSERT INTO positions (portfolio_id, symbol, quantity, buy_price, buy_date,
                                   stop_loss, target, notes, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s) RETURNING id
        """, (portfolio_id, symbol.upper(), quantity, buy_price, buy_date, stop_loss, target, notes, now, now))
        row = cur.fetchone()
        return row["id"] if row else 0
    else:
        cur.execute("""
            INSERT INTO positions (portfolio_id, symbol, quantity, buy_price, buy_date,
                                   stop_loss, target, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (portfolio_id, symbol.upper(), quantity, buy_price, buy_date, stop_loss, target, notes, now, now))
        conn.commit()
        return cur.lastrowid

def close_position(position_id: int, sell_price: float, sell_date: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not sell_date:
        sell_date = datetime.now().strftime("%Y-%m-%d")
    execute_db("""
        UPDATE positions SET sell_price=?, sell_date=?, status='CLOSED', updated_at=?
        WHERE id=?
    """, (sell_price, sell_date, now, position_id))

def update_position(position_id: int, **kwargs):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    allowed = {"quantity", "buy_price", "buy_date", "sell_price", "sell_date",
               "stop_loss", "target", "notes", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now
    
    pg = is_postgresql()
    if pg and not hasattr(_local, "conn"):
        set_clause = ", ".join(f"{k}=%s" for k in updates)
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(f"UPDATE positions SET {set_clause} WHERE id=%s", list(updates.values()) + [position_id])
    else:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        execute_db(f"UPDATE positions SET {set_clause} WHERE id=?", list(updates.values()) + [position_id])

def delete_position(position_id: int):
    execute_db("DELETE FROM positions WHERE id=?", (position_id,))

def get_positions(portfolio_id: int, status: str = None) -> list[dict]:
    if status:
        rows = execute_db(
            "SELECT * FROM positions WHERE portfolio_id=? AND status=? ORDER BY buy_date DESC",
            (portfolio_id, status.upper()), fetch="all")
    else:
        rows = execute_db(
            "SELECT * FROM positions WHERE portfolio_id=? ORDER BY status ASC, buy_date DESC",
            (portfolio_id,), fetch="all")
    return [dict(r) for r in rows]

def get_position(position_id: int) -> dict | None:
    row = execute_db("SELECT * FROM positions WHERE id=?", (position_id,), fetch="one")
    return dict(row) if row else None

def get_portfolio_summary(portfolio_id: int) -> dict:
    open_pos = execute_db(
        "SELECT COUNT(*) as cnt, SUM(quantity * buy_price) as invested FROM positions WHERE portfolio_id=? AND status='OPEN'",
        (portfolio_id,), fetch="one")
    closed_pos = execute_db("""
        SELECT COUNT(*) as cnt,
               SUM((sell_price - buy_price) * quantity) as realized_pnl,
               SUM(quantity * buy_price) as total_cost
        FROM positions WHERE portfolio_id=? AND status='CLOSED'
    """, (portfolio_id,), fetch="one")
    
    # SQLite vs PG: RealDictCursor or sqlite3.Row
    invested = open_pos.get("invested") or 0 if open_pos else 0
    open_cnt = open_pos.get("cnt") or 0 if open_pos else 0
    realized_pnl = closed_pos.get("realized_pnl") or 0 if closed_pos else 0
    closed_cnt = closed_pos.get("cnt") or 0 if closed_pos else 0
    total_traded = closed_pos.get("total_cost") or 0 if closed_pos else 0

    return {
        "open_count": open_cnt,
        "invested": round(float(invested), 2),
        "closed_count": closed_cnt,
        "realized_pnl": round(float(realized_pnl), 2),
        "total_traded": round(float(total_traded), 2),
    }

def db_stats() -> dict:
    """Get DB statistics."""
    results = execute_db("SELECT COUNT(*) as cnt FROM scan_results", fetch="count")
    history = execute_db("SELECT COUNT(*) as cnt FROM score_history", fetch="count")
    meta = execute_db("SELECT COUNT(*) as cnt FROM scan_meta", fetch="count")
    
    size_kb = 0.0
    if not is_postgresql():
        size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        size_kb = round(size / 1024, 1)
    
    return {
        "results": results,
        "history_records": history,
        "meta_entries": meta,
        "db_size_kb": size_kb,
        "backend": "PostgreSQL/Supabase" if is_postgresql() else "SQLite"
    }
