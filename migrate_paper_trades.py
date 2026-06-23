"""
Paper Trades Migration: Supabase → Railway PostgreSQL
=====================================================
Exports paper_trades from Supabase and imports into Railway PG.
Run AFTER setting up Railway PostgreSQL and getting the new DATABASE_URL.

Usage:
    python migrate_paper_trades.py <RAILWAY_DATABASE_URL>

Example:
    python migrate_paper_trades.py "postgresql://postgres:xxx@xxx.railway.internal:5432/railway"
"""

import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# ── Source: Supabase (hardcoded from current .env) ──
SUPABASE_URL = "postgresql://postgres.yrrxwhiivdbmcqhiinsj:Quant00DB1314@aws-1-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require"

# ── Paper trades CREATE TABLE (from db.py) ──
CREATE_PAPER_TRADES = """
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_trades_entry ON paper_trades(entry_date);
"""

# Columns to migrate (all except id — will be auto-generated)
COLUMNS = [
    "symbol", "sector", "entry_date", "entry_price", "target_price",
    "stop_loss", "virtual_capital", "quantity", "score_at_entry",
    "grade_at_entry", "technical_score", "fundamental_score",
    "earnings_momentum_score", "earnings_grade", "smart_money_score",
    "sector_rotation_score", "catalyst_score", "news_sentiment_score",
    "risk_score", "risk_reward", "model_version", "market_regime",
    "nifty_entry", "high_conviction", "is_golden", "signals_json",
    "earnings_signals_json", "exit_date", "exit_price", "exit_reason",
    "nifty_exit", "days_held", "return_pct", "alpha_pct",
    "max_drawdown_pct", "max_runup_pct", "status", "position_size_pct",
    "weight_version", "confidence_score", "entry_rank",
    "breadth_advances", "breadth_declines", "breadth_ratio",
    "probability_bucket", "expected_return_bucket", "created_at",
]


def migrate(railway_url: str):
    # ── Step 1: Export from Supabase ──
    print("[1/4] Connecting to Supabase...")
    src = psycopg2.connect(SUPABASE_URL, cursor_factory=RealDictCursor, connect_timeout=10)
    src_cur = src.cursor()

    print("[2/4] Exporting paper_trades from Supabase...")
    src_cur.execute("SELECT * FROM paper_trades ORDER BY id")
    rows = src_cur.fetchall()
    print(f"       Found {len(rows)} paper trades")

    if not rows:
        print("       No paper trades to migrate. Done!")
        src.close()
        return

    # Show summary
    open_count = sum(1 for r in rows if r.get("status") == "OPEN")
    closed_count = sum(1 for r in rows if r.get("status") != "OPEN")
    print(f"       OPEN: {open_count} | CLOSED: {closed_count}")

    src.close()

    # ── Step 2: Connect to Railway ──
    if not railway_url.startswith("postgresql://"):
        railway_url = railway_url.replace("postgres://", "postgresql://", 1)
    if "sslmode" not in railway_url:
        railway_url += ("&" if "?" in railway_url else "?") + "sslmode=require"

    print(f"[3/4] Connecting to Railway PG...")
    dst = psycopg2.connect(railway_url, connect_timeout=10)
    dst_cur = dst.cursor()

    # Create table
    dst_cur.execute(CREATE_PAPER_TRADES)
    dst.commit()
    print("       paper_trades table created on Railway")

    # ── Step 3: Insert data ──
    print(f"[4/4] Importing {len(rows)} paper trades into Railway...")

    # Build values list
    values = []
    for row in rows:
        vals = []
        for col in COLUMNS:
            vals.append(row.get(col))
        values.append(tuple(vals))

    cols_str = ", ".join(COLUMNS)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    insert_sql = f"INSERT INTO paper_trades ({cols_str}) VALUES ({placeholders})"

    # Batch insert
    batch_size = 100
    inserted = 0
    for i in range(0, len(values), batch_size):
        batch = values[i:i + batch_size]
        dst_cur.executemany(insert_sql, batch)
        inserted += len(batch)
        print(f"       Inserted {inserted}/{len(values)}...")

    dst.commit()
    dst.close()

    print(f"\n✅ Migration complete! {len(rows)} paper trades moved to Railway PG.")
    print(f"   OPEN: {open_count} | CLOSED: {closed_count}")
    print(f"\n📌 Next steps:")
    print(f"   1. Update DATABASE_URL in Railway app env vars to: {railway_url.split('@')[1].split('/')[0]}...")
    print(f"   2. Redeploy the app")
    print(f"   3. Verify in logs: 'PG pool created'")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python migrate_paper_trades.py <RAILWAY_DATABASE_URL>")
        print('Example: python migrate_paper_trades.py "postgresql://postgres:xxx@xxx.railway.internal:5432/railway"')
        sys.exit(1)

    railway_url = sys.argv[1]
    print("=" * 60)
    print("  Paper Trades Migration: Supabase -> Railway PostgreSQL")
    print("=" * 60)
    migrate(railway_url)
