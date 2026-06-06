"""
SQLite and PostgreSQL database wrapper for Smart Screener.
Stores scan results, historical scores, metadata, and normalized analytics tables.

Phase 1 Changes:
- Replaced thread-local PG connections with ThreadedConnectionPool (psycopg2.pool)
- maxconn dynamically reads MAX_DB_CONNECTIONS env variable (default 10)
- execute_db() now uses pool.getconn()/putconn() with proper finally clause
- SQLite path uses fresh connect() per call (no thread-local, WAL mode)
- All direct cursor usage (remove_custom_stock, create_portfolio, add_position,
  update_position) migrated to execute_db()
- Added pool_status(), pg_cooldown_active() helpers
- Added _collect_result() and _execute_sqlite() helpers
- Removed _local threading.local() global, _get_conn(), _get_sqlite_conn()
"""

import os
import json
import logging
import threading
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from metrics.timer import timed

log = logging.getLogger("db")

DB_PATH = Path(__file__).parent / "cache" / "screener.db"

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def is_postgresql() -> bool:
    """Check if PostgreSQL is configured."""
    return bool(
        DATABASE_URL and (
            DATABASE_URL.startswith("postgres://")
            or DATABASE_URL.startswith("postgresql://")
        )
    )

# ─── ThreadedConnectionPool (Phase 1) ───

_pg_pool = None
_pg_pool_lock = threading.Lock()
_pg_cooldown_until = 0.0

def _normalize_pg_url(url: str) -> str:
    """Ensure URL uses postgresql:// scheme and has sslmode set."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url

def _get_pg_pool():
    """Lazy-initialise a ThreadedConnectionPool. Returns None if PG unavailable."""
    global _pg_pool, _pg_cooldown_until
    if _pg_pool is not None:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        now = time.time()
        if now < _pg_cooldown_until:
            return None  # still in cooldown after a failure
        try:
            import psycopg2.pool
            url = _normalize_pg_url(DATABASE_URL)
            max_conn = int(os.getenv("MAX_DB_CONNECTIONS", "15"))
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=max_conn,
                dsn=url,
                connect_timeout=3,
            )
            log.info("PG pool created (minconn=1, maxconn=%d)", max_conn)
        except Exception as exc:
            log.error("PG pool failed: %s — SQLite fallback 60s", exc)
            _pg_cooldown_until = time.time() + 60
            return None
    return _pg_pool

def pg_cooldown_active() -> bool:
    """True if PG is currently under cooldown."""
    return time.time() < _pg_cooldown_until

def pool_status() -> dict:
    """Return the current pool health for /api/health."""
    return {
        "pg_pool_available": _pg_pool is not None,
        "pg_cooldown_active": pg_cooldown_active(),
        "pg_cooldown_remaining_s": max(0, round(_pg_cooldown_until - time.time())),
    }

# ─── Type helpers ───

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

# ─── Result collectors ───

def _collect_result(cur, fetch: str):
    """Extract the requested result shape from a cursor (PG path)."""
    if fetch == "one":
        return cur.fetchone()
    if fetch == "all":
        return cur.fetchall()
    if fetch == "count":
        row = cur.fetchone()
        return list(row.values())[0] if row else 0
    if fetch == "rowcount":
        return cur.rowcount
    if fetch == "lastrowid":
        return cur.lastrowid
    return None

def _execute_sqlite(query: str, params, fetch: str):
    """Fresh-connection SQLite executor — no thread-local, WAL mode enabled."""
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(str(DB_PATH), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        cur = conn.cursor()
        cur.execute(query, params or ())
        if fetch == "one":
            row = cur.fetchone()
            return dict(row) if row else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        if fetch == "count":
            row = cur.fetchone()
            return row[0] if row else 0
        if fetch == "rowcount":
            return cur.rowcount
        if fetch == "lastrowid":
            return cur.lastrowid
        conn.commit()
        return None

# ─── Unified executor ───

def execute_db(query: str, params=None, fetch: str = None):
    """
    Unified query executor for PostgreSQL and SQLite.
    - PG path: uses ThreadedConnectionPool, always returns connection to pool.
    - SQLite path: fresh connect() per call (WAL mode, thread-safe reads).
    - Automatically translates '?' placeholders to '%s' for PG.
    - Falls through to SQLite on any PG failure (with 60s cooldown).
    - Pool exhaustion: if getconn() would block, falls through to SQLite
      immediately instead of hanging the Flask request thread.
    """
    global _pg_cooldown_until, _pg_pool
    from metrics import counters
    counters.inc("db_queries")

    if params is not None:
        params = tuple(_to_native(v) for v in params)

    if is_postgresql():
        pool = _get_pg_pool()
        if pool:
            conn = None
            try:
                from psycopg2.extras import RealDictCursor
                conn = pool.getconn()
                conn.autocommit = True
                query_pg = query.replace("?", "%s")
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query_pg, params or ())
                    return _collect_result(cur, fetch)
            except Exception as exc:
                if "PoolError" in type(exc).__name__ or "connection pool exhausted" in str(exc).lower():
                    # Pool exhausted — retry once after 50ms (connection likely freed)
                    conn = None  # no connection to return
                    time.sleep(0.05)
                    try:
                        conn = pool.getconn()
                        conn.autocommit = True
                        query_pg = query.replace("?", "%s")
                        with conn.cursor(cursor_factory=RealDictCursor) as cur:
                            cur.execute(query_pg, params or ())
                            return _collect_result(cur, fetch)
                    except Exception:
                        log.warning("PG pool exhausted after retry, falling back to SQLite | Query: %.100s", query)
                        counters.inc("db_pool_exhausted")
                        if conn:
                            try:
                                pool.putconn(conn)
                            except Exception:
                                pass
                            conn = None
                        # fall through to SQLite (no cooldown — pool is fine, just busy)
                else:
                    log.error("PG execute failed: %s | Query: %.200s", exc, query)
                    counters.inc("db_failures")
                    _pg_cooldown_until = time.time() + 60
                    # Destroy the failed connection so the pool doesn't reuse it
                    if conn:
                        try:
                            pool.putconn(conn, close=True)
                        except Exception:
                            pass
                        conn = None
                    # fall through to SQLite
            finally:
                if conn:
                    try:
                        pool.putconn(conn)
                    except Exception:
                        pass

    return _execute_sqlite(query, params, fetch)

# ─── Database Initialisation ───

def init_db():
    """Create tables if they don't exist.
    
    Uses an explicit temporary connection rather than the pool so that
    DDL runs atomically even when called before the pool is initialised.
    """
    if is_postgresql():
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            url = _normalize_pg_url(DATABASE_URL)
            conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=5)
            conn.autocommit = True
            try:
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
                try:
                    cur.execute("ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS detailed_json JSONB;")
                except Exception as e:
                    log.warning("ALTER TABLE fundamentals detailed_json failed: %s", e)

                # Phase 6: scan state tables
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS scan_runs (
                        scan_id TEXT PRIMARY KEY,
                        mode TEXT NOT NULL DEFAULT 'manual',
                        status TEXT NOT NULL DEFAULT 'running',
                        phase TEXT DEFAULT '',
                        start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        end_time TIMESTAMP,
                        processed_count INTEGER DEFAULT 0,
                        failed_count INTEGER DEFAULT 0,
                        deferred_count INTEGER DEFAULT 0,
                        candidate_count INTEGER DEFAULT 0,
                        duration_seconds REAL,
                        error_message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_scan_runs_status ON scan_runs(status);

                    CREATE TABLE IF NOT EXISTS current_scan_state (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        scan_id TEXT,
                        mode TEXT DEFAULT '',
                        status TEXT DEFAULT 'idle',
                        phase TEXT DEFAULT '',
                        start_time TIMESTAMP,
                        processed_count INTEGER DEFAULT 0,
                        failed_count INTEGER DEFAULT 0,
                        candidate_count INTEGER DEFAULT 0,
                        cancel_requested INTEGER DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO current_scan_state (id, status, cancel_requested, updated_at)
                    VALUES (1, 'idle', 0, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO NOTHING;
                """)

                # Phase 7: symbol freshness tracking
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS symbol_state (
                        symbol TEXT PRIMARY KEY,
                        last_price_update TIMESTAMP,
                        last_technical_update TIMESTAMP,
                        last_news_update TIMESTAMP,
                        last_sentiment_update TIMESTAMP,
                        last_financial_update TIMESTAMP,
                        last_deep_scan TIMESTAMP,
                        price_change_pct REAL DEFAULT 0.0,
                        prev_score INTEGER DEFAULT 0,
                        needs_deep_scan INTEGER DEFAULT 0,
                        deep_scan_reason TEXT DEFAULT '',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # ── Release 3: Outcome Intelligence Layer ──────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS paper_trades (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        sector TEXT DEFAULT '',
                        entry_date TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        target_price REAL,
                        stop_loss REAL,
                        virtual_capital REAL DEFAULT 25000,
                        quantity INTEGER DEFAULT 0,
                        -- Component scores at entry
                        score_at_entry INTEGER DEFAULT 0,
                        grade_at_entry TEXT DEFAULT '',
                        technical_score REAL DEFAULT 0,
                        fundamental_score REAL DEFAULT 0,
                        earnings_momentum_score REAL DEFAULT 0,
                        earnings_grade TEXT DEFAULT '',
                        smart_money_score REAL DEFAULT 0,
                        sector_rotation_score REAL DEFAULT 0,
                        catalyst_score REAL DEFAULT 0,
                        news_sentiment_score REAL DEFAULT 0,
                        risk_score REAL DEFAULT 0,
                        risk_reward REAL DEFAULT 0,
                        -- Regime snapshot
                        model_version TEXT DEFAULT '',
                        market_regime TEXT DEFAULT '',
                        nifty_entry REAL,
                        high_conviction INTEGER DEFAULT 0,
                        is_golden INTEGER DEFAULT 0,
                        signals_json TEXT DEFAULT '[]',
                        earnings_signals_json TEXT DEFAULT '[]',
                        -- Exit
                        exit_date TEXT,
                        exit_price REAL,
                        exit_reason TEXT,
                        nifty_exit REAL,
                        days_held INTEGER DEFAULT 0,
                        return_pct REAL,
                        alpha_pct REAL,
                        max_drawdown_pct REAL DEFAULT 0,
                        max_runup_pct REAL DEFAULT 0,
                        -- Status
                        status TEXT DEFAULT 'OPEN',
                        position_size_pct REAL DEFAULT 20.0,
                        weight_version TEXT DEFAULT '',
                        confidence_score REAL DEFAULT 0,
                        entry_rank INTEGER DEFAULT 0,
                        -- Market breadth at entry
                        breadth_advances INTEGER DEFAULT 0,
                        breadth_declines INTEGER DEFAULT 0,
                        breadth_ratio REAL DEFAULT 0,
                        -- R4 prep (NULL until calibrated)
                        probability_bucket TEXT,
                        expected_return_bucket TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
                    CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
                    CREATE INDEX IF NOT EXISTS idx_paper_trades_entry ON paper_trades(entry_date);

                    CREATE TABLE IF NOT EXISTS recommendation_snapshots (
                        id SERIAL PRIMARY KEY,
                        snapshot_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        rank INTEGER NOT NULL,
                        score INTEGER DEFAULT 0,
                        grade TEXT DEFAULT '',
                        technical_score REAL DEFAULT 0,
                        fundamental_score REAL DEFAULT 0,
                        earnings_momentum_score REAL DEFAULT 0,
                        earnings_grade TEXT DEFAULT '',
                        smart_money_score REAL DEFAULT 0,
                        risk_score REAL DEFAULT 0,
                        price REAL DEFAULT 0,
                        model_version TEXT DEFAULT '',
                        market_regime TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (snapshot_date, symbol)
                    );
                    CREATE INDEX IF NOT EXISTS idx_rec_snap_date ON recommendation_snapshots(snapshot_date);

                    CREATE TABLE IF NOT EXISTS paper_portfolio_daily (
                        date TEXT PRIMARY KEY,
                        portfolio_value REAL DEFAULT 0,
                        invested_value REAL DEFAULT 0,
                        open_positions INTEGER DEFAULT 0,
                        closed_today INTEGER DEFAULT 0,
                        total_closed INTEGER DEFAULT 0,
                        win_count INTEGER DEFAULT 0,
                        loss_count INTEGER DEFAULT 0,
                        total_return_pct REAL DEFAULT 0,
                        nifty_level REAL DEFAULT 0,
                        model_version TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # P5: Performance indexes for dashboard queries
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_scan_results_score ON scan_results(score DESC);
                    CREATE INDEX IF NOT EXISTS idx_scan_results_hc ON scan_results(high_conviction) WHERE high_conviction = 1;
                    CREATE INDEX IF NOT EXISTS idx_paper_trades_model ON paper_trades(model_version);
                    CREATE INDEX IF NOT EXISTS idx_news_articles_symbol ON news_articles(symbol);
                """)

                log.info("PostgreSQL tables checked/created.")
            finally:
                conn.close()
        except Exception as exc:
            log.error("init_db PG failed: %s — falling back to SQLite init", exc)

    # Always init SQLite tables as safety net for pool-exhaustion fallback
    _init_sqlite()

    auto_clear_daily_cache()


def _init_sqlite():
    """Create SQLite tables using a single fresh connection."""
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(str(DB_PATH), timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
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
        # Add detailed_json column if missing (idempotent)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(fundamentals)")
            cols = [col[1] for col in cur.fetchall()]
            if "detailed_json" not in cols:
                conn.execute("ALTER TABLE fundamentals ADD COLUMN detailed_json TEXT;")
                conn.commit()
        except Exception as e:
            log.warning("SQLite ALTER TABLE fundamentals detailed_json failed: %s", e)

        # Phase 6: scan state tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                scan_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'running',
                phase TEXT DEFAULT '',
                start_time TEXT DEFAULT (datetime('now')),
                end_time TEXT,
                processed_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                deferred_count INTEGER DEFAULT 0,
                candidate_count INTEGER DEFAULT 0,
                duration_seconds REAL,
                error_message TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_scan_runs_status ON scan_runs(status);

            CREATE TABLE IF NOT EXISTS current_scan_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                scan_id TEXT,
                mode TEXT DEFAULT '',
                status TEXT DEFAULT 'idle',
                phase TEXT DEFAULT '',
                start_time TEXT,
                processed_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                candidate_count INTEGER DEFAULT 0,
                cancel_requested INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO current_scan_state (id, status, cancel_requested, updated_at)
            VALUES (1, 'idle', 0, datetime('now'));

            CREATE TABLE IF NOT EXISTS symbol_state (
                symbol TEXT PRIMARY KEY,
                last_price_update TEXT,
                last_technical_update TEXT,
                last_news_update TEXT,
                last_sentiment_update TEXT,
                last_financial_update TEXT,
                last_deep_scan TEXT,
                price_change_pct REAL DEFAULT 0.0,
                prev_score INTEGER DEFAULT 0,
                needs_deep_scan INTEGER DEFAULT 0,
                deep_scan_reason TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # ── Release 3: Outcome Intelligence Layer ──────────────
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                sector TEXT DEFAULT '',
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                target_price REAL,
                stop_loss REAL,
                virtual_capital REAL DEFAULT 25000,
                quantity INTEGER DEFAULT 0,
                score_at_entry INTEGER DEFAULT 0,
                grade_at_entry TEXT DEFAULT '',
                technical_score REAL DEFAULT 0,
                fundamental_score REAL DEFAULT 0,
                earnings_momentum_score REAL DEFAULT 0,
                earnings_grade TEXT DEFAULT '',
                smart_money_score REAL DEFAULT 0,
                sector_rotation_score REAL DEFAULT 0,
                catalyst_score REAL DEFAULT 0,
                news_sentiment_score REAL DEFAULT 0,
                risk_score REAL DEFAULT 0,
                risk_reward REAL DEFAULT 0,
                model_version TEXT DEFAULT '',
                market_regime TEXT DEFAULT '',
                nifty_entry REAL,
                high_conviction INTEGER DEFAULT 0,
                is_golden INTEGER DEFAULT 0,
                signals_json TEXT DEFAULT '[]',
                earnings_signals_json TEXT DEFAULT '[]',
                exit_date TEXT,
                exit_price REAL,
                exit_reason TEXT,
                nifty_exit REAL,
                days_held INTEGER DEFAULT 0,
                return_pct REAL,
                alpha_pct REAL,
                max_drawdown_pct REAL DEFAULT 0,
                max_runup_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'OPEN',
                position_size_pct REAL DEFAULT 20.0,
                weight_version TEXT DEFAULT '',
                confidence_score REAL DEFAULT 0,
                entry_rank INTEGER DEFAULT 0,
                breadth_advances INTEGER DEFAULT 0,
                breadth_declines INTEGER DEFAULT 0,
                breadth_ratio REAL DEFAULT 0,
                probability_bucket TEXT,
                expected_return_bucket TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
            CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_paper_trades_entry ON paper_trades(entry_date);

            CREATE TABLE IF NOT EXISTS recommendation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score INTEGER DEFAULT 0,
                grade TEXT DEFAULT '',
                technical_score REAL DEFAULT 0,
                fundamental_score REAL DEFAULT 0,
                earnings_momentum_score REAL DEFAULT 0,
                earnings_grade TEXT DEFAULT '',
                smart_money_score REAL DEFAULT 0,
                risk_score REAL DEFAULT 0,
                price REAL DEFAULT 0,
                model_version TEXT DEFAULT '',
                market_regime TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE (snapshot_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_rec_snap_date ON recommendation_snapshots(snapshot_date);

            CREATE TABLE IF NOT EXISTS paper_portfolio_daily (
                date TEXT PRIMARY KEY,
                portfolio_value REAL DEFAULT 0,
                invested_value REAL DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                closed_today INTEGER DEFAULT 0,
                total_closed INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                total_return_pct REAL DEFAULT 0,
                nifty_level REAL DEFAULT 0,
                model_version TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # P5: Performance indexes for dashboard queries (parity with PostgreSQL path)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_scan_results_score ON scan_results(score DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_results_hc ON scan_results(high_conviction);
            CREATE INDEX IF NOT EXISTS idx_news_articles_symbol ON news_articles(symbol);
        """)

        log.info("SQLite Database initialized: %s", DB_PATH)


def auto_clear_daily_cache():
    """Clear local SQLite database and/or table scan_results when calendar date changes."""
    try:
        from datetime import date
        from pathlib import Path
        cache_dir = Path(__file__).parent / "cache"
        cache_dir.mkdir(exist_ok=True)
        clear_tracker = cache_dir / "last_clear_date.txt"
        today_str = date.today().isoformat()

        # If the file doesn't exist, we write it and DO NOT clear results (fresh setup).
        if not clear_tracker.exists():
            clear_tracker.write_text(today_str)
            log.info("Daily cache clear tracker initialized for %s", today_str)
            return

        last_date = clear_tracker.read_text().strip()
        if last_date != today_str:
            log.info("Daily cache auto-clear triggered (Last run: %s, Today: %s). Clearing results...", last_date, today_str)
            execute_db("DELETE FROM scan_results")
            clear_tracker.write_text(today_str)
            log.info("Daily cache successfully cleared for %s", today_str)
    except Exception as exc:
        log.warning("Daily cache auto-clear failed: %s", exc)

# ─── Unified Scan State (Phase 6) ───

import uuid as _uuid

_SCAN_LOCK_TIMEOUT_MIN = 30  # stale scan recovery threshold


class ScanState:
    """DB-backed scan state. Single source of truth for scan progress.
    Uses current_scan_state (single row, O(1) reads) + scan_runs (history).
    """

    def start(self, total: int, mode: str = "manual") -> str:
        """Start a new scan. Returns scan_id."""
        self._recover_stale()
        scan_id = f"scan_{mode}_{int(time.time())}_{_uuid.uuid4().hex[:6]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_db("""
            INSERT INTO scan_runs (scan_id, mode, status, phase, start_time, candidate_count, created_at)
            VALUES (?, ?, 'running', 'init', ?, ?, ?)
        """, (scan_id, mode, now, total, now))
        execute_db("""
            UPDATE current_scan_state SET
                scan_id=?, mode=?, status='running', phase='init',
                start_time=?, processed_count=0, failed_count=0,
                candidate_count=?, cancel_requested=0, updated_at=?
            WHERE id=1
        """, (scan_id, mode, now, total, now))
        self._scan_id = scan_id
        self._total = total
        return scan_id

    def update(self, **kwargs):
        """Update scan progress. Accepts: phase, processed_count, failed_count, candidate_count."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        allowed = {"phase", "processed_count", "failed_count", "candidate_count"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        # Update current_scan_state
        execute_db(
            f"UPDATE current_scan_state SET {set_clause}, updated_at=? WHERE id=1",
            vals + [now]
        )
        # Update scan_runs too
        scan_id = getattr(self, "_scan_id", None)
        if scan_id:
            execute_db(
                f"UPDATE scan_runs SET {set_clause} WHERE scan_id=?",
                vals + [scan_id]
            )

    def set_progress(self, value: int):
        """Convenience: update processed_count."""
        self.update(processed_count=value)

    def complete(self, success: bool = True, error_message: str = ""):
        """Mark scan as complete."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "completed" if success else "failed"
        scan_id = getattr(self, "_scan_id", None)
        if scan_id:
            # Calculate duration
            row = execute_db("SELECT start_time FROM scan_runs WHERE scan_id=?", (scan_id,), fetch="one")
            duration = 0.0
            if row and row.get("start_time"):
                try:
                    st = datetime.fromisoformat(str(row["start_time"]))
                    duration = (datetime.now() - st).total_seconds()
                except Exception:
                    pass
            execute_db("""
                UPDATE scan_runs SET status=?, end_time=?, duration_seconds=?, error_message=?
                WHERE scan_id=?
            """, (status, now, round(duration, 1), error_message or None, scan_id))
        execute_db("""
            UPDATE current_scan_state SET
                status='idle', phase='', cancel_requested=0, updated_at=?
            WHERE id=1
        """, (now,))
        self._scan_id = None

    def finish(self):
        """Alias for complete(success=True). Backward compat with old ScanState."""
        self.complete(success=True)

    @property
    def is_scanning(self) -> bool:
        self._recover_stale()
        row = execute_db("SELECT status FROM current_scan_state WHERE id=1", fetch="one")
        return row["status"] == "running" if row else False

    @property
    def cancel_requested(self) -> bool:
        row = execute_db("SELECT cancel_requested FROM current_scan_state WHERE id=1", fetch="one")
        return bool(row["cancel_requested"]) if row else False

    @cancel_requested.setter
    def cancel_requested(self, value: bool):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_db(
            "UPDATE current_scan_state SET cancel_requested=?, updated_at=? WHERE id=1",
            (1 if value else 0, now)
        )

    def status(self) -> dict:
        """Return current scan state as dict. O(1) read."""
        self._recover_stale()
        row = execute_db("SELECT * FROM current_scan_state WHERE id=1", fetch="one")
        if not row:
            return {"scanning": False, "status": "idle", "progress": 0, "total": 0}
        return {
            "scanning": row["status"] == "running",
            "status": row["status"],
            "mode": row.get("mode", ""),
            "phase": row.get("phase", ""),
            "progress": row.get("processed_count", 0),
            "total": row.get("candidate_count", 0),
            "failed": row.get("failed_count", 0),
            "scan_id": row.get("scan_id", ""),
            "cancel_requested": bool(row.get("cancel_requested", 0)),
        }

    def _recover_stale(self):
        """Auto-recover scans stuck in 'running' for > SCAN_LOCK_TIMEOUT_MIN."""
        try:
            row = execute_db("SELECT * FROM current_scan_state WHERE id=1", fetch="one")
            if not row or row["status"] != "running":
                return
            updated = row.get("updated_at")
            if not updated:
                return
            try:
                last_update = datetime.fromisoformat(str(updated))
                age_min = (datetime.now() - last_update).total_seconds() / 60
                if age_min > _SCAN_LOCK_TIMEOUT_MIN:
                    scan_id = row.get("scan_id", "")
                    log.warning("Stale scan detected (scan_id=%s, age=%.1f min). Recovering...", scan_id, age_min)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if scan_id:
                        execute_db("""
                            UPDATE scan_runs SET status='failed', end_time=?, error_message='stale_recovery'
                            WHERE scan_id=?
                        """, (now, scan_id))
                    execute_db("""
                        UPDATE current_scan_state SET status='idle', phase='', cancel_requested=0, updated_at=?
                        WHERE id=1
                    """, (now,))
            except Exception:
                pass
        except Exception:
            pass


def get_recent_scan_runs(limit: int = 10) -> list:
    """Get recent scan runs for admin dashboard."""
    rows = execute_db(
        "SELECT * FROM scan_runs ORDER BY created_at DESC LIMIT ?",
        (limit,), fetch="all"
    )
    return [dict(r) for r in rows] if rows else []

scan_state = ScanState()


# ─── Symbol Freshness Tracking (Phase 7) ───

def get_symbol_state(symbol: str) -> dict | None:
    """Get freshness state for a symbol."""
    row = execute_db("SELECT * FROM symbol_state WHERE symbol=?", (symbol,), fetch="one")
    return dict(row) if row else None


def set_symbol_state(symbol: str, **kwargs):
    """Partial update of symbol state. Only provided fields are changed."""
    allowed = {
        "last_price_update", "last_technical_update", "last_news_update",
        "last_sentiment_update", "last_financial_update", "last_deep_scan",
        "price_change_pct", "prev_score", "needs_deep_scan", "deep_scan_reason",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Upsert
    existing = execute_db("SELECT symbol FROM symbol_state WHERE symbol=?", (symbol,), fetch="one")
    if existing:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        execute_db(
            f"UPDATE symbol_state SET {set_clause}, updated_at=? WHERE symbol=?",
            vals + [now, symbol]
        )
    else:
        updates["symbol"] = symbol
        updates["updated_at"] = now
        cols = ", ".join(updates.keys())
        placeholders = ", ".join("?" for _ in updates)
        execute_db(
            f"INSERT INTO symbol_state ({cols}) VALUES ({placeholders})",
            list(updates.values())
        )


def get_symbols_needing_deep_scan(limit: int = 100) -> list[str]:
    """Get symbols flagged for deep scan, prioritized by need + staleness."""
    rows = execute_db("""
        SELECT symbol FROM symbol_state
        WHERE needs_deep_scan = 1
        ORDER BY last_deep_scan ASC NULLS FIRST
        LIMIT ?
    """, (limit,), fetch="all")
    return [r["symbol"] for r in rows] if rows else []


def mark_deep_scan_needed(symbol: str, reason: str = ""):
    """Flag a symbol as needing deep scan."""
    set_symbol_state(symbol, needs_deep_scan=1, deep_scan_reason=reason)


def bulk_update_symbol_state(updates: list[dict]):
    """Batch update symbol states. Each dict must have 'symbol' key + fields to update."""
    for u in updates:
        sym = u.pop("symbol", None)
        if sym:
            set_symbol_state(sym, **u)



import hashlib
from dataclasses import dataclass, field
from pathlib import Path as _Path

_DLQ_FILE = _Path(__file__).parent / "cache" / "dead_letter_queue.jsonl"
_deferred_writes: list = []
_deferred_lock = threading.Lock()
_DLQ_MAX_RETRIES = 3


@dataclass
class DeferredBatch:
    batch_id: str = ""
    created_at: str = ""
    retry_count: int = 0
    symbols: list = field(default_factory=list)
    results: list = field(default_factory=list)
    checksum: str = ""


def _compute_checksum(results: list) -> str:
    raw = json.dumps([r.get("symbol", "") for r in results], sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def queue_deferred_write(results: list):
    """Queue failed writes for retry. Checksum-based dedup."""
    if not results:
        return
    cs = _compute_checksum(results)
    with _deferred_lock:
        # Dedup by checksum
        for existing in _deferred_writes:
            if existing.checksum == cs:
                return
        batch = DeferredBatch(
            batch_id=f"dlq_{int(time.time())}_{cs}",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            retry_count=0,
            symbols=[r.get("symbol", "") for r in results],
            results=results,
            checksum=cs,
        )
        _deferred_writes.append(batch)
    log.warning("DLQ: Queued %d results (batch=%s)", len(results), batch.batch_id)


def flush_deferred_writes() -> int:
    """Retry all deferred writes. Returns count of successfully flushed."""
    flushed = 0
    with _deferred_lock:
        remaining = []
        for batch in _deferred_writes:
            try:
                # Try to save without DLQ fallback (avoid infinite recursion)
                _save_results_raw(batch.results)
                flushed += len(batch.results)
                log.info("DLQ: Flushed batch %s (%d results)", batch.batch_id, len(batch.results))
            except Exception as exc:
                batch.retry_count += 1
                if batch.retry_count >= _DLQ_MAX_RETRIES:
                    _move_to_dlq(batch)
                else:
                    remaining.append(batch)
                    log.warning("DLQ: Retry %d/%d failed for batch %s: %s",
                                batch.retry_count, _DLQ_MAX_RETRIES, batch.batch_id, exc)
        _deferred_writes.clear()
        _deferred_writes.extend(remaining)
    return flushed


def _save_results_raw(results: list):
    """Raw save without DLQ fallback (used by flush to avoid recursion)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scan_date = datetime.now().strftime("%Y-%m-%d")
    for r in results:
        sym = r["symbol"]
        execute_db("""
            INSERT INTO scan_results (symbol, data, score, high_conviction, sector, scan_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                data=excluded.data, score=excluded.score,
                high_conviction=excluded.high_conviction, sector=excluded.sector,
                scan_date=excluded.scan_date, updated_at=excluded.updated_at
        """, (sym, json.dumps(r), r.get("score", 0), 1 if r.get("high_conviction") else 0,
              r.get("sector", ""), scan_date, now))


def _move_to_dlq(batch: DeferredBatch):
    """Persist failed batch to JSONL file. NEVER silently drop data."""
    try:
        entry = {
            "batch_id": batch.batch_id,
            "created_at": batch.created_at,
            "retry_count": batch.retry_count,
            "symbols": batch.symbols,
            "results": batch.results,
            "moved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(_DLQ_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        log.error("DLQ: Batch %s moved to dead-letter queue after %d retries (%d results). FILE: %s",
                   batch.batch_id, batch.retry_count, len(batch.results), _DLQ_FILE)
    except Exception as exc:
        log.critical("DLQ: FAILED to write to DLQ file: %s -- DATA MAY BE LOST for batch %s", exc, batch.batch_id)


def replay_dlq() -> int:
    """Re-attempt all DLQ entries. Returns count replayed successfully."""
    if not _DLQ_FILE.exists():
        return 0
    replayed = 0
    remaining_lines = []
    try:
        with open(_DLQ_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    _save_results_raw(entry.get("results", []))
                    replayed += len(entry.get("results", []))
                    log.info("DLQ replay: batch %s replayed OK", entry.get("batch_id", "?"))
                except Exception:
                    remaining_lines.append(line)
        # Rewrite file with only failed entries
        with open(_DLQ_FILE, "w", encoding="utf-8") as f:
            for line in remaining_lines:
                f.write(line + "\n")
    except Exception as exc:
        log.warning("DLQ replay failed: %s", exc)
    return replayed


def dlq_entry_count() -> int:
    """Count entries in DLQ file."""
    if not _DLQ_FILE.exists():
        return 0
    try:
        with open(_DLQ_FILE, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


# ─── Scan Results ───

@timed("db_write_batch")
def save_results(results: list[dict], meta: dict = None):
    """Save scan results to DB and populate normalized tables.
    Phase 5: staleness guard + DLQ fallback on failure.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scan_date = datetime.now().strftime("%Y-%m-%d")

    saved_count = 0
    for r in results:
        sym = r["symbol"]

        # Phase 5: Staleness guard -- don't overwrite today's deep scan with fast scan
        new_mode = r.get("scan_mode", "fast")
        if new_mode == "fast":
            try:
                existing = execute_db(
                    "SELECT data FROM scan_results WHERE symbol = ? AND scan_date = ?",
                    (sym, scan_date), fetch="one"
                )
                if existing:
                    existing_data = _parse_data_column(existing["data"])
                    if existing_data and existing_data.get("scan_mode") == "deep":
                        log.debug("Staleness guard: skipping fast overwrite of deep scan for %s", sym)
                        continue
            except Exception:
                pass  # proceed with write if check fails

        try:
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
            execute_db("DELETE FROM news_articles WHERE symbol=?", (sym,))
            gdelt_data = r.get("gdelt", {})
            articles = gdelt_data.get("articles", [])
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
                True if r.get("is_breakout") else False, True if r.get("vp_divergence") else False, r.get("weekly_trend", "flat"),
                True if r.get("below_ema200") else False, r.get("high_52w"), r.get("low_52w"), r.get("pullback_pct"), now
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
                r.get("score", 0), r.get("grade", ""), True if r.get("high_conviction") else False, True if r.get("bear_play") else False,
                True if r.get("is_golden") else False, now
            ))

            saved_count += 1
        except Exception as exc:
            log.warning("DB write failed for %s: %s -- queueing to DLQ", sym, exc)
            queue_deferred_write([r])

    if meta:
        for k, v in meta.items():
            set_meta(k, v)

    log.info("Saved %d/%d results to DB", saved_count, len(results))

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

def _ensure_trade_populated(r):
    if not r:
        return r
    if "trade" in r and r["trade"] and "entry_low" in r["trade"]:
        return r
    try:
        current_price = r.get("price")
        if not current_price:
            current_price = r.get("close") or r.get("ltp") or r.get("last_price")
        if not current_price:
            return r
        current_price = float(current_price)
        if current_price <= 0:
            return r

        atr_pct = r.get("atr_pct", 2.0)
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 2.0
        atr_pct = float(atr_pct)

        atr_val = (atr_pct * current_price) / 100
        sr = r.get("support_resistance", {})
        s1 = sr.get("s1")
        if s1 is not None: s1 = float(s1)
        s2 = sr.get("s2")
        if s2 is not None: s2 = float(s2)
        pivot = sr.get("pivot")
        if pivot is not None: pivot = float(pivot)
        fib_s = r.get("fib_support")
        if fib_s is not None: fib_s = float(fib_s)

        atr_stop = current_price - (2.0 * atr_val)
        sl_candidates = [atr_stop]
        if s1 and s1 < current_price and (current_price - s1) / current_price <= 0.07:
            sl_candidates.append(s1 * 0.99)
        if fib_s and fib_s < current_price and (current_price - fib_s) / current_price <= 0.07:
            sl_candidates.append(fib_s * 0.99)
        valid_supports = [s for s in sl_candidates if s < current_price]
        if valid_supports:
            structural_sl = max(valid_supports)
            if current_price - structural_sl < current_price * 0.015:
                structural_sl = min(valid_supports)
        else:
            structural_sl = atr_stop

        strict_sl = round(structural_sl, 2)
        if strict_sl >= current_price or strict_sl <= 0:
            strict_sl = round(current_price * 0.97, 2)

        weekly_trend = r.get("weekly_trend", "flat")
        adx = r.get("adx")
        if adx is None:
            adx = 20.0
        else:
            adx = float(adx)

        macd_signal = r.get("macd_signal", "Bearish")
        base_mult = 2.0
        if weekly_trend == "up" and macd_signal == "Bullish":
            base_mult = 3.0
        elif weekly_trend == "down":
            base_mult = 1.8
        if adx > 25:
            base_mult += 0.5

        risk_distance = current_price - strict_sl
        if risk_distance <= 0:
            risk_distance = current_price * 0.03
            strict_sl = round(current_price - risk_distance, 2)

        default_target = current_price + (base_mult * risk_distance)
        target_candidates = [default_target]
        r1 = sr.get("r1")
        if r1 is not None: r1 = float(r1)
        r2 = sr.get("r2")
        if r2 is not None: r2 = float(r2)
        fib_r = r.get("fib_resistance")
        if fib_r is not None: fib_r = float(fib_r)

        if fib_r and fib_r > current_price * 1.02:
            target_candidates.append(fib_r)
        if r1 and r1 > current_price * 1.02:
            target_candidates.append(r1)
        realistic = [t for t in target_candidates if t <= default_target * 1.5]
        target_price = max(realistic) if realistic else default_target
        if target_price <= current_price:
            target_price = default_target

        risk_dist = current_price - strict_sl
        risk_reward = round((target_price - current_price) / risk_dist, 1) if risk_dist > 0 else 0.0
        target1 = round(r1 if (r1 and r1 > current_price) else target_price, 2)
        target2 = round(r2 if (r2 and r2 > target1) else target1 * 1.08, 2)
        target3 = round(target2 * 1.10, 2)
        rr1_val = round((target1 - current_price) / risk_dist, 1) if risk_dist > 0 else 1.5
        rr2_val = round((target2 - current_price) / risk_dist, 1) if risk_dist > 0 else 2.5
        rr3_val = round((target3 - current_price) / risk_dist, 1) if risk_dist > 0 else 3.5

        is_breakout = r.get("is_breakout", False)
        if is_breakout:
            breakout_level = r1 or fib_r or current_price
            if breakout_level > current_price * 0.95 and breakout_level < current_price * 1.05:
                entry_low = round(breakout_level * 0.995, 2)
                entry_high = round(breakout_level * 1.015, 2)
            else:
                entry_low = round(current_price * 0.995, 2)
                entry_high = round(current_price * 1.01, 2)
        else:
            pullback_supports = []
            if s1 and s1 < current_price and (current_price - s1) / current_price <= 0.03:
                pullback_supports.append(s1)
            if pivot and pivot < current_price and (current_price - pivot) / current_price <= 0.03:
                pullback_supports.append(pivot)
            if pullback_supports:
                entry_low = round(max(pullback_supports), 2)
                if entry_low >= current_price:
                    entry_low = round(current_price * 0.99, 2)
                entry_high = round(current_price * 1.005, 2)
            else:
                entry_low = round(current_price * 0.99, 2)
                entry_high = round(current_price * 1.005, 2)

        if entry_low > entry_high:
            entry_low, entry_high = entry_high, entry_low

        regime = r.get("market_regime") or get_meta("market_regime", "unknown")
        if regime == "bearish":
            booking_plan = "Book 100% at Target 1 (Bear Market defensive play)"
        elif weekly_trend == "up":
            booking_plan = "Book 50% at Target 1, trail 50% to Target 2 with SL at Cost"
        else:
            booking_plan = "Book 70% at Target 1, trail 30% with tight trailing SL"

        r["trade"] = {
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": strict_sl,
            "target1": target1,
            "rr1": rr1_val,
            "target2": target2,
            "rr2": rr2_val,
            "target3": target3,
            "rr3": rr3_val,
            "booking_plan": booking_plan,
            "risk_reward": risk_reward,
            "target_1": target1,
            "target_2": target2,
        }
    except Exception as e:
        log.warning("Fallback trade generation failed: %s", e)
    return r

def _parse_data_column(val):
    if not val:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}

def load_results(limit: int = 750) -> list[dict]:
    """Load scan results from DB, ordered by score."""
    t0 = time.perf_counter()
    rows = execute_db("SELECT data FROM scan_results ORDER BY score DESC LIMIT ?", (limit,), fetch="all")
    t_query = round((time.perf_counter() - t0) * 1000, 2)
    
    t0 = time.perf_counter()
    results = []
    for row in rows:
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                results.append(r)
        except Exception:
            pass
    t_parse = round((time.perf_counter() - t0) * 1000, 2)
    
    log.info("[DB PERF] load_results limit=%d | query=%s ms | parse_json=%s ms | total_rows=%d", limit, t_query, t_parse, len(results))
    print(f"[DB PERF] load_results limit={limit} | query={t_query} ms | parse_json={t_parse} ms | total_rows={len(results)}", flush=True)
    return results


def load_golden_results(limit: int = 100) -> list[dict]:
    """Load Golden stocks from DB, ordered by score.
    
    Uses PG JSONB syntax when PostgreSQL is active, with automatic
    fallback to SQLite json_extract if PG connection fails mid-request.
    """
    pg_query = "SELECT data FROM scan_results WHERE (data->>'is_golden')::text = 'true' OR (data->>'is_golden')::text = '1' ORDER BY score DESC LIMIT ?"
    sqlite_query = "SELECT data FROM scan_results WHERE json_extract(data, '$.is_golden') = 1 OR json_extract(data, '$.is_golden') = 'true' ORDER BY score DESC LIMIT ?"
    try:
        query = pg_query if is_postgresql() and not pg_cooldown_active() else sqlite_query
        rows = execute_db(query, (limit,), fetch="all")
    except Exception:
        rows = _execute_sqlite(sqlite_query, (limit,), "all")
    results = []
    for row in (rows or []):
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                results.append(r)
        except Exception:
            pass
    return results


def load_breakout_results(limit: int = 100) -> list[dict]:
    """Load Breakout stocks from DB, ordered by score.
    
    Uses PG JSONB syntax when PostgreSQL is active, with automatic
    fallback to SQLite json_extract if PG connection fails mid-request.
    """
    pg_query = "SELECT data FROM scan_results WHERE (data->>'is_breakout')::text = 'true' OR (data->>'is_breakout')::text = '1' ORDER BY score DESC LIMIT ?"
    sqlite_query = "SELECT data FROM scan_results WHERE json_extract(data, '$.is_breakout') = 1 OR json_extract(data, '$.is_breakout') = 'true' ORDER BY score DESC LIMIT ?"
    try:
        query = pg_query if is_postgresql() and not pg_cooldown_active() else sqlite_query
        rows = execute_db(query, (limit,), fetch="all")
    except Exception:
        rows = _execute_sqlite(sqlite_query, (limit,), "all")
    results = []
    for row in (rows or []):
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                results.append(r)
        except Exception:
            pass
    return results


def load_high_conviction_results(limit: int = 100) -> list[dict]:
    """Load High Conviction stocks from DB, ordered by score."""
    query = "SELECT data FROM scan_results WHERE high_conviction = 1 ORDER BY score DESC LIMIT ?"
    rows = execute_db(query, (limit,), fetch="all")
    results = []
    for row in rows:
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                results.append(r)
        except Exception:
            pass
    return results

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
    if row:
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                return r
        except Exception:
            pass
    return None

def save_detailed_fundamentals(symbol: str, data: dict):
    """Save processed detailed financials JSON to fundamentals table."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    v = json.dumps(data)
    execute_db("""
        INSERT INTO fundamentals (symbol, detailed_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET detailed_json=excluded.detailed_json, updated_at=excluded.updated_at
    """, (symbol.upper(), v, now))

def get_detailed_fundamentals(symbol: str) -> dict | None:
    """Get stored detailed financials JSON from fundamentals table."""
    row = execute_db("SELECT detailed_json FROM fundamentals WHERE symbol=?", (symbol.upper(),), fetch="one")
    if row and row.get("detailed_json"):
        try:
            return json.loads(row["detailed_json"])
        except Exception:
            pass
    return None

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
    res = {}
    for row in rows:
        try:
            r = _parse_data_column(row["data"])
            if r:
                _ensure_trade_populated(r)
                res[row["symbol"]] = r
        except Exception:
            pass
    return res

def get_all_symbols() -> list[str]:
    """Get all symbols in scan_results."""
    rows = execute_db("SELECT symbol FROM scan_results ORDER BY score DESC", fetch="all")
    return [row["symbol"] for row in rows]


def get_all_results() -> list[dict]:
    """Get all scan results as dicts. Used for deep scan shortlisting."""
    rows = execute_db("SELECT symbol, data FROM scan_results ORDER BY score DESC", fetch="all")
    results = []
    for row in rows:
        try:
            r = _parse_data_column(row["data"])
            if r:
                results.append(r)
        except Exception:
            pass
    return results

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
    if is_postgresql():
        execute_db(f"DELETE FROM scan_results WHERE updated_at < NOW() - INTERVAL '{days} days'")
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
    """Delete a custom stock using execute_db (fully migrated away from direct cursor)."""
    rowcount = execute_db(
        "DELETE FROM custom_stocks WHERE symbol=?",
        (symbol.upper(),),
        fetch="rowcount"
    )
    return bool(rowcount and rowcount > 0)

def get_custom_stocks() -> list[dict]:
    rows = execute_db("SELECT symbol, exchange, added_at, note FROM custom_stocks ORDER BY added_at DESC", fetch="all")
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
    """Create a new portfolio and return its ID (fully migrated to execute_db)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if is_postgresql():
        # PostgreSQL: use RETURNING id clause via a raw pool query
        pool = _get_pg_pool()
        if pool:
            conn = None
            try:
                from psycopg2.extras import RealDictCursor
                conn = pool.getconn()
                conn.autocommit = True
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "INSERT INTO portfolios (name, description, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING id",
                        (name, description, now, now)
                    )
                    row = cur.fetchone()
                    return row["id"] if row else 0
            except Exception as exc:
                log.error("create_portfolio PG failed: %s", exc)
                return 0
            finally:
                if conn:
                    try:
                        pool.putconn(conn)
                    except Exception:
                        pass
        # Fallback: SQLite
    return execute_db(
        "INSERT INTO portfolios (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, description, now, now),
        fetch="lastrowid"
    ) or 0

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
    """Insert a new position and return its ID (fully migrated to execute_db)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if is_postgresql():
        pool = _get_pg_pool()
        if pool:
            conn = None
            try:
                from psycopg2.extras import RealDictCursor
                conn = pool.getconn()
                conn.autocommit = True
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        INSERT INTO positions (portfolio_id, symbol, quantity, buy_price, buy_date,
                                               stop_loss, target, notes, status, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s) RETURNING id
                    """, (portfolio_id, symbol.upper(), quantity, buy_price, buy_date, stop_loss, target, notes, now, now))
                    row = cur.fetchone()
                    return row["id"] if row else 0
            except Exception as exc:
                log.error("add_position PG failed: %s", exc)
                return 0
            finally:
                if conn:
                    try:
                        pool.putconn(conn)
                    except Exception:
                        pass
        # Fallback: SQLite
    return execute_db("""
        INSERT INTO positions (portfolio_id, symbol, quantity, buy_price, buy_date,
                               stop_loss, target, notes, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
    """, (portfolio_id, symbol.upper(), quantity, buy_price, buy_date, stop_loss, target, notes, now, now),
    fetch="lastrowid") or 0

def close_position(position_id: int, sell_price: float, sell_date: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not sell_date:
        sell_date = datetime.now().strftime("%Y-%m-%d")
    execute_db("""
        UPDATE positions SET sell_price=?, sell_date=?, status='CLOSED', updated_at=?
        WHERE id=?
    """, (sell_price, sell_date, now, position_id))

def update_position(position_id: int, **kwargs):
    """Update allowed position fields using execute_db (fully migrated)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    allowed = {"quantity", "buy_price", "buy_date", "sell_price", "sell_date",
               "stop_loss", "target", "notes", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now
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
    """Get DB statistics including pool health."""
    results_cnt = execute_db("SELECT COUNT(*) as cnt FROM scan_results", fetch="count")
    history_cnt = execute_db("SELECT COUNT(*) as cnt FROM score_history", fetch="count")
    meta_cnt = execute_db("SELECT COUNT(*) as cnt FROM scan_meta", fetch="count")

    size_kb = 0.0
    if not is_postgresql() or not _pg_pool:
        size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        size_kb = round(size / 1024, 1)

    backend = "PostgreSQL/Supabase" if (is_postgresql() and _pg_pool) else ("SQLite (Fallback)" if is_postgresql() else "SQLite")

    return {
        "results": results_cnt,
        "history_records": history_cnt,
        "meta_entries": meta_cnt,
        "db_size_kb": size_kb,
        "backend": backend,
        **pool_status(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RELEASE 3 — OUTCOME INTELLIGENCE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

_PAPER_VIRTUAL_CAPITAL = 25000  # ₹25,000 per pick
_PAPER_MAX_HOLD_DAYS = 20       # 20 trading days
_PAPER_COOLDOWN_DAYS = 5        # Don't re-pick within 5 days
_PAPER_TOP_N_SNAPSHOT = 20      # Snapshot top 20 daily
_PAPER_TOP_N_TRADES = 5         # Paper trade top 5


def create_paper_trade(stock_data: dict, nifty_price: float = None,
                       market_regime: str = "unknown") -> int | None:
    """Create a paper trade entry from scan result data.
    Returns the trade ID, or None if duplicate cooldown applies.
    """
    sym = stock_data.get("symbol", "")
    if not sym:
        return None

    entry_date = datetime.now().strftime("%Y-%m-%d")

    # Duplicate prevention: 5-day cooldown
    existing = execute_db(
        "SELECT id, entry_date FROM paper_trades WHERE symbol = ? AND status = 'OPEN'",
        (sym,), fetch="one"
    )
    if existing:
        return None  # already has an open position

    recent = execute_db(
        "SELECT entry_date FROM paper_trades WHERE symbol = ? ORDER BY entry_date DESC LIMIT 1",
        (sym,), fetch="one"
    )
    if recent:
        from datetime import date as _date
        try:
            last_dt = _date.fromisoformat(recent["entry_date"])
            today_dt = _date.fromisoformat(entry_date)
            if (today_dt - last_dt).days < _PAPER_COOLDOWN_DAYS:
                # Allow if score improved by 10+
                prev_score = execute_db(
                    "SELECT score_at_entry FROM paper_trades WHERE symbol = ? ORDER BY entry_date DESC LIMIT 1",
                    (sym,), fetch="one"
                )
                curr_score = stock_data.get("score", 0)
                if prev_score and curr_score - prev_score.get("score_at_entry", 0) < 10:
                    log.debug("Paper trade cooldown: %s picked %s days ago", sym, (today_dt - last_dt).days)
                    return None
        except Exception:
            pass

    entry_price = stock_data.get("price", 0)
    if entry_price <= 0:
        return None

    quantity = max(1, int(_PAPER_VIRTUAL_CAPITAL / entry_price))

    execute_db("""
        INSERT INTO paper_trades (
            symbol, sector, entry_date, entry_price, target_price, stop_loss,
            virtual_capital, quantity,
            score_at_entry, grade_at_entry,
            technical_score, fundamental_score, earnings_momentum_score, earnings_grade,
            smart_money_score, sector_rotation_score, catalyst_score, news_sentiment_score,
            risk_score, risk_reward,
            model_version, market_regime, nifty_entry,
            high_conviction, is_golden, signals_json, earnings_signals_json,
            weight_version, confidence_score, entry_rank,
            breadth_advances, breadth_declines, breadth_ratio,
            status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        sym,
        stock_data.get("sector", ""),
        entry_date,
        entry_price,
        stock_data.get("target_price"),
        stock_data.get("stop_loss"),
        _PAPER_VIRTUAL_CAPITAL,
        quantity,
        stock_data.get("score", 0),
        stock_data.get("grade", ""),
        stock_data.get("technical_score", 0),
        stock_data.get("fundamental_score", 0),
        stock_data.get("earnings_momentum_score", 0),
        stock_data.get("earnings_grade", ""),
        stock_data.get("smart_money_score", 0),
        stock_data.get("sector_rotation_score", 0),
        stock_data.get("marketaux_catalyst_score", 0),
        stock_data.get("news_sentiment_score", 0),
        stock_data.get("risk_score", 0),
        stock_data.get("risk_reward", 0),
        stock_data.get("model_version", ""),
        market_regime,
        nifty_price,
        1 if stock_data.get("high_conviction") else 0,
        1 if stock_data.get("is_golden") else 0,
        json.dumps(stock_data.get("signals", [])[:10]),
        json.dumps(stock_data.get("earnings_signals", [])[:10]),
        stock_data.get("weight_version", "R2.1"),
        stock_data.get("confidence_score", 0),
        stock_data.get("_entry_rank", 0),
        stock_data.get("_breadth_advances", 0),
        stock_data.get("_breadth_declines", 0),
        stock_data.get("_breadth_ratio", 0),
        "OPEN",
    ))

    trade_id = execute_db("SELECT MAX(id) as id FROM paper_trades WHERE symbol = ?", (sym,), fetch="one")
    log.info("[PaperTrade] ENTRY: %s @ ₹%.2f, score=%d, grade=%s",
             sym, entry_price, stock_data.get("score", 0), stock_data.get("grade", ""))
    return trade_id["id"] if trade_id else None


def close_paper_trade(trade_id: int, exit_price: float, exit_reason: str,
                      nifty_price: float = None) -> bool:
    """Close a paper trade with outcome data."""
    trade = execute_db("SELECT * FROM paper_trades WHERE id = ?", (trade_id,), fetch="one")
    if not trade or trade["status"] != "OPEN":
        return False

    entry_price = trade["entry_price"]
    return_pct = ((exit_price - entry_price) / entry_price) * 100

    # Alpha vs Nifty
    alpha_pct = None
    nifty_entry = trade.get("nifty_entry")
    if nifty_entry and nifty_price and nifty_entry > 0:
        nifty_return = ((nifty_price - nifty_entry) / nifty_entry) * 100
        alpha_pct = return_pct - nifty_return

    # Days held
    from datetime import date as _date
    try:
        entry_dt = _date.fromisoformat(trade["entry_date"])
        exit_dt = _date.today()
        days_held = (exit_dt - entry_dt).days
    except Exception:
        days_held = 0

    exit_date = datetime.now().strftime("%Y-%m-%d")

    execute_db("""
        UPDATE paper_trades SET
            exit_date=?, exit_price=?, exit_reason=?, nifty_exit=?,
            days_held=?, return_pct=?, alpha_pct=?,
            status='CLOSED'
        WHERE id=?
    """, (exit_date, exit_price, exit_reason, nifty_price,
          days_held, round(return_pct, 2), round(alpha_pct, 2) if alpha_pct else None,
          trade_id))

    log.info("[PaperTrade] EXIT: %s @ ₹%.2f (%s), return=%.2f%%, alpha=%.2f%%, held=%d days",
             trade["symbol"], exit_price, exit_reason, return_pct,
             alpha_pct or 0, days_held)
    return True


def update_paper_trade_extremes(trade_id: int, current_price: float):
    """Update max drawdown and max runup for an open trade."""
    trade = execute_db("SELECT entry_price, max_drawdown_pct, max_runup_pct FROM paper_trades WHERE id = ?",
                       (trade_id,), fetch="one")
    if not trade:
        return

    entry = trade["entry_price"]
    current_pct = ((current_price - entry) / entry) * 100
    new_dd = min(trade.get("max_drawdown_pct") or 0, current_pct)
    new_ru = max(trade.get("max_runup_pct") or 0, current_pct)

    execute_db(
        "UPDATE paper_trades SET max_drawdown_pct=?, max_runup_pct=? WHERE id=?",
        (round(new_dd, 2), round(new_ru, 2), trade_id)
    )


def get_open_paper_trades() -> list[dict]:
    """Get all open paper trades."""
    return execute_db(
        "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY entry_date",
        fetch="all"
    ) or []


def get_all_paper_trades(limit: int = 200) -> list[dict]:
    """Get all paper trades (open + closed), latest first."""
    return execute_db(
        "SELECT * FROM paper_trades ORDER BY entry_date DESC LIMIT ?",
        (limit,), fetch="all"
    ) or []


def save_recommendation_snapshot(snapshot_date: str, ranked_stocks: list[dict],
                                  market_regime: str = "unknown"):
    """Save daily top-N recommendation snapshot for calibration."""
    for i, stock in enumerate(ranked_stocks[:_PAPER_TOP_N_SNAPSHOT]):
        try:
            execute_db("""
                INSERT INTO recommendation_snapshots (
                    snapshot_date, symbol, rank, score, grade,
                    technical_score, fundamental_score,
                    earnings_momentum_score, earnings_grade,
                    smart_money_score, risk_score, price,
                    model_version, market_regime
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_date, symbol) DO UPDATE SET
                    rank=excluded.rank, score=excluded.score
            """, (
                snapshot_date,
                stock.get("symbol", ""),
                i + 1,
                stock.get("score", 0),
                stock.get("grade", ""),
                stock.get("technical_score", 0),
                stock.get("fundamental_score", 0),
                stock.get("earnings_momentum_score", 0),
                stock.get("earnings_grade", ""),
                stock.get("smart_money_score", 0),
                stock.get("risk_score", 0),
                stock.get("price", 0),
                stock.get("model_version", ""),
                market_regime,
            ))
        except Exception as exc:
            log.debug("snapshot save failed for %s: %s", stock.get("symbol"), exc)

    log.info("[PaperTrade] Saved snapshot: %d stocks for %s", min(len(ranked_stocks), _PAPER_TOP_N_SNAPSHOT), snapshot_date)


def save_portfolio_daily(nifty_price: float = None):
    """Save daily equity curve point."""
    today = datetime.now().strftime("%Y-%m-%d")

    open_trades = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='OPEN'", fetch="one")
    closed_total = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED'", fetch="one")
    closed_today_r = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND exit_date=?", (today,), fetch="one")
    wins = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND return_pct > 0", fetch="one")
    losses = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND return_pct <= 0", fetch="one")
    avg_return = execute_db("SELECT AVG(return_pct) as avg FROM paper_trades WHERE status='CLOSED'", fetch="one")

    # Portfolio value: sum of open positions at current virtual capital
    open_value = execute_db("SELECT SUM(virtual_capital) as total FROM paper_trades WHERE status='OPEN'", fetch="one")

    execute_db("""
        INSERT INTO paper_portfolio_daily (date, portfolio_value, invested_value,
            open_positions, closed_today, total_closed, win_count, loss_count,
            total_return_pct, nifty_level, model_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            portfolio_value=excluded.portfolio_value,
            open_positions=excluded.open_positions,
            closed_today=excluded.closed_today,
            total_closed=excluded.total_closed,
            win_count=excluded.win_count,
            loss_count=excluded.loss_count,
            total_return_pct=excluded.total_return_pct,
            nifty_level=excluded.nifty_level
    """, (
        today,
        (open_value or {}).get("total") or 0,
        (open_value or {}).get("total") or 0,
        (open_trades or {}).get("cnt") or 0,
        (closed_today_r or {}).get("cnt") or 0,
        (closed_total or {}).get("cnt") or 0,
        (wins or {}).get("cnt") or 0,
        (losses or {}).get("cnt") or 0,
        round((avg_return or {}).get("avg") or 0, 2),
        nifty_price or 0,
        "R2.1",
    ))


def get_paper_trade_stats() -> dict:
    """Get aggregated paper trading statistics."""
    t_start = time.perf_counter()
    
    t0 = time.perf_counter()
    total = execute_db("SELECT COUNT(*) as cnt FROM paper_trades", fetch="one")
    closed = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED'", fetch="one")
    wins = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND return_pct > 0", fetch="one")
    avg_ret = execute_db("SELECT AVG(return_pct) as avg FROM paper_trades WHERE status='CLOSED'", fetch="one")
    avg_days = execute_db("SELECT AVG(days_held) as avg FROM paper_trades WHERE status='CLOSED'", fetch="one")
    avg_dd = execute_db("SELECT AVG(max_drawdown_pct) as avg FROM paper_trades WHERE status='CLOSED'", fetch="one")
    avg_alpha = execute_db("SELECT AVG(alpha_pct) as avg FROM paper_trades WHERE status='CLOSED' AND alpha_pct IS NOT NULL", fetch="one")
    best = execute_db("SELECT symbol, return_pct FROM paper_trades WHERE status='CLOSED' ORDER BY return_pct DESC LIMIT 1", fetch="one")
    worst = execute_db("SELECT symbol, return_pct FROM paper_trades WHERE status='CLOSED' ORDER BY return_pct ASC LIMIT 1", fetch="one")
    t_basic = round((time.perf_counter() - t0) * 1000, 2)

    total_cnt = (total or {}).get("cnt") or 0
    closed_cnt = (closed or {}).get("cnt") or 0
    win_cnt = (wins or {}).get("cnt") or 0

    t0 = time.perf_counter()
    # Profit Factor & Expectancy
    sum_wins = execute_db("SELECT SUM(return_pct) as total FROM paper_trades WHERE status='CLOSED' AND return_pct > 0", fetch="one")
    sum_losses = execute_db("SELECT SUM(ABS(return_pct)) as total FROM paper_trades WHERE status='CLOSED' AND return_pct <= 0", fetch="one")
    loss_cnt = closed_cnt - win_cnt
    total_win = (sum_wins or {}).get("total") or 0
    total_loss = (sum_losses or {}).get("total") or 0
    profit_factor = round(total_win / total_loss, 2) if total_loss > 0 else 0
    avg_win = total_win / win_cnt if win_cnt > 0 else 0
    avg_loss = total_loss / loss_cnt if loss_cnt > 0 else 0
    win_rate_dec = win_cnt / closed_cnt if closed_cnt > 0 else 0
    expectancy = round((win_rate_dec * avg_win) - ((1 - win_rate_dec) * avg_loss), 2) if closed_cnt > 0 else 0
    t_expectancy = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    # Golden Stock win rate
    golden_total = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND is_golden=1", fetch="one")
    golden_wins = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND is_golden=1 AND return_pct > 0", fetch="one")
    golden_cnt = (golden_total or {}).get("cnt") or 0
    golden_win_cnt = (golden_wins or {}).get("cnt") or 0

    # High Conviction win rate
    hc_total = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND high_conviction=1", fetch="one")
    hc_wins = execute_db("SELECT COUNT(*) as cnt FROM paper_trades WHERE status='CLOSED' AND high_conviction=1 AND return_pct > 0", fetch="one")
    hc_cnt = (hc_total or {}).get("cnt") or 0
    hc_win_cnt = (hc_wins or {}).get("cnt") or 0
    t_conviction = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    # Factor attribution: winning vs losing trades
    factor_win = execute_db("""
        SELECT AVG(technical_score) as tech, AVG(fundamental_score) as fund,
               AVG(earnings_momentum_score) as earn, AVG(smart_money_score) as smart,
               AVG(risk_score) as risk, AVG(score_at_entry) as score
        FROM paper_trades WHERE status='CLOSED' AND return_pct > 0
    """, fetch="one") or {}
    factor_loss = execute_db("""
        SELECT AVG(technical_score) as tech, AVG(fundamental_score) as fund,
               AVG(earnings_momentum_score) as earn, AVG(smart_money_score) as smart,
               AVG(risk_score) as risk, AVG(score_at_entry) as score
        FROM paper_trades WHERE status='CLOSED' AND return_pct <= 0
    """, fetch="one") or {}
    t_factor = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    # Max single-trade drawdown
    max_dd = execute_db("SELECT MIN(max_drawdown_pct) as dd FROM paper_trades WHERE status='CLOSED'", fetch="one")

    # By model version
    by_version = execute_db("""
        SELECT model_version, COUNT(*) as trades,
               SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) as wins,
               AVG(return_pct) as avg_return,
               AVG(alpha_pct) as avg_alpha
        FROM paper_trades WHERE status='CLOSED'
        GROUP BY model_version ORDER BY model_version
    """, fetch="all") or []

    # By sector
    by_sector = execute_db("""
        SELECT sector, COUNT(*) as trades,
               AVG(return_pct) as avg_return
        FROM paper_trades WHERE status='CLOSED'
        GROUP BY sector ORDER BY avg_return DESC LIMIT 10
    """, fetch="all") or []

    # By regime
    by_regime = execute_db("""
        SELECT market_regime, COUNT(*) as trades,
               AVG(return_pct) as avg_return
        FROM paper_trades WHERE status='CLOSED'
        GROUP BY market_regime
    """, fetch="all") or []
    t_groups = round((time.perf_counter() - t0) * 1000, 2)

    total_ms = round((time.perf_counter() - t_start) * 1000, 2)
    log.info("[DB PERF] get_paper_trade_stats | total_queries=21 | t_basic=%s ms | t_expectancy=%s ms | t_conviction=%s ms | t_factor=%s ms | t_groups=%s ms | total=%s ms", t_basic, t_expectancy, t_conviction, t_factor, t_groups, total_ms)
    print(f"[DB PERF] get_paper_trade_stats | total_queries=21 | t_basic={t_basic} ms | t_expectancy={t_expectancy} ms | t_conviction={t_conviction} ms | t_factor={t_factor} ms | t_groups={t_groups} ms | total={total_ms} ms", flush=True)

    def _r(v): return round(v or 0, 2)

    return {
        "total_trades": total_cnt,
        "open_trades": total_cnt - closed_cnt,
        "closed_trades": closed_cnt,
        "win_rate": round((win_cnt / closed_cnt * 100), 1) if closed_cnt > 0 else 0,
        "avg_return_pct": _r((avg_ret or {}).get("avg")),
        "avg_days_held": round((avg_days or {}).get("avg") or 0, 1),
        "avg_drawdown_pct": _r((avg_dd or {}).get("avg")),
        "max_drawdown_pct": _r((max_dd or {}).get("dd")),
        "avg_alpha_pct": _r((avg_alpha or {}).get("avg")),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "best_trade": {"symbol": best["symbol"], "return_pct": best["return_pct"]} if best else None,
        "worst_trade": {"symbol": worst["symbol"], "return_pct": worst["return_pct"]} if worst else None,
        # Conviction breakdowns
        "golden_stock": {
            "trades": golden_cnt,
            "win_rate": round(golden_win_cnt / golden_cnt * 100, 1) if golden_cnt > 0 else 0,
        },
        "high_conviction": {
            "trades": hc_cnt,
            "win_rate": round(hc_win_cnt / hc_cnt * 100, 1) if hc_cnt > 0 else 0,
        },
        # Factor attribution
        "factor_attribution": {
            "winners": {
                "avg_score": _r(factor_win.get("score")),
                "avg_technical": _r(factor_win.get("tech")),
                "avg_fundamental": _r(factor_win.get("fund")),
                "avg_earnings": _r(factor_win.get("earn")),
                "avg_smart_money": _r(factor_win.get("smart")),
                "avg_risk": _r(factor_win.get("risk")),
            },
            "losers": {
                "avg_score": _r(factor_loss.get("score")),
                "avg_technical": _r(factor_loss.get("tech")),
                "avg_fundamental": _r(factor_loss.get("fund")),
                "avg_earnings": _r(factor_loss.get("earn")),
                "avg_smart_money": _r(factor_loss.get("smart")),
                "avg_risk": _r(factor_loss.get("risk")),
            },
        },
        "by_model_version": [
            {"version": r["model_version"], "trades": r["trades"],
             "win_rate": round(r["wins"] / r["trades"] * 100, 1) if r["trades"] > 0 else 0,
             "avg_return": _r(r["avg_return"]),
             "avg_alpha": _r(r["avg_alpha"])}
            for r in by_version
        ],
        "by_sector": [
            {"sector": r["sector"], "trades": r["trades"], "avg_return": _r(r["avg_return"])}
            for r in by_sector
        ],
        "by_regime": [
            {"regime": r["market_regime"], "trades": r["trades"], "avg_return": _r(r["avg_return"])}
            for r in by_regime
        ],
    }


def get_equity_curve(days: int = 90) -> list[dict]:
    """Get equity curve data for charting."""
    return execute_db(
        "SELECT * FROM paper_portfolio_daily ORDER BY date DESC LIMIT ?",
        (days,), fetch="all"
    ) or []
