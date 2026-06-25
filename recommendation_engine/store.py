"""RE-3 Recommendation store (RE-2A §4) — the single canonical RO read/write path.

Tables (additive, idempotent, cross-DB via db.execute_db):
  * recommendations          — immutable, versioned RO core + payloads + audit
  * recommendation_live_state — single-writer mutable runtime overlay (RE-1G §5)

P1: adds `scan_mode` (supersession authority, O1) + batched upsert (O2). No consumer reads
these yet (shadow).
"""
import json

import db

_DDL_REC = """
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    exchange          TEXT DEFAULT 'NSE',
    scan_id           TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    formula_versions  TEXT,
    input_hash        TEXT,
    generated_at_utc  TEXT NOT NULL,
    ttl_sec           INTEGER DEFAULT 86400,
    status            TEXT NOT NULL,
    eligible          INTEGER DEFAULT 0,
    supersedes_id     TEXT,
    scan_mode         TEXT,
    core              TEXT NOT NULL,
    payloads          TEXT,
    audit             TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_DDL_LIVE = """
CREATE TABLE IF NOT EXISTS recommendation_live_state (
    recommendation_id   TEXT PRIMARY KEY,
    status              TEXT,
    cmp                 REAL,
    mfe                 REAL,
    mae                 REAL,
    trailing_sl_current REAL,
    hits                TEXT,
    outcome             TEXT,
    monitor_version     TEXT,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Canonical column order — shared by single + batch upsert so they stay identical.
_COLS = ["recommendation_id", "symbol", "exchange", "scan_id", "schema_version",
         "model_version", "formula_versions", "input_hash", "generated_at_utc",
         "ttl_sec", "status", "eligible", "supersedes_id", "scan_mode",
         "core", "payloads", "audit"]
_UPDATE = ("status=excluded.status, eligible=excluded.eligible, scan_mode=excluded.scan_mode, "
           "core=excluded.core, payloads=excluded.payloads, audit=excluded.audit, "
           "generated_at_utc=excluded.generated_at_utc, model_version=excluded.model_version")


def init_recommendation_store():
    """Idempotent table creation + additive scan_mode column. Safe to call repeatedly."""
    db.execute_db(_DDL_REC)
    db.execute_db(_DDL_LIVE)
    try:                                  # additive for pre-P1 tables (cross-DB, no IF NOT EXISTS)
        db.execute_db("ALTER TABLE recommendations ADD COLUMN scan_mode TEXT")
    except Exception:
        pass                              # column already exists
    db.execute_db("CREATE INDEX IF NOT EXISTS idx_rec_symbol_status ON recommendations(symbol, status)")
    db.execute_db("CREATE INDEX IF NOT EXISTS idx_rec_scan ON recommendations(scan_id)")


def _to_row(ro: dict) -> tuple:
    """Flatten an RO into the canonical column tuple (shared single/batch)."""
    m = ro["meta"]
    core = {k: ro[k] for k in ("engines", "scoring", "eligibility", "trade",
                               "sizing", "allocation", "presentation", "inputs_snapshot")}
    isnap = ro.get("inputs_snapshot", {})
    return (m["recommendation_id"], m["symbol"], m.get("exchange", "NSE"), m["scan_id"],
            m["schema_version"], m["model_version"], json.dumps(ro["audit"]["formula_versions"]),
            m.get("input_hash") or isnap.get("input_hash"), m["generated_at_utc"],
            m.get("ttl_sec", 86400), m["status"], 1 if ro["eligibility"]["eligible"] else 0,
            m.get("supersedes_id"), isnap.get("scan_mode"),
            json.dumps(core), json.dumps(ro["payloads"]), json.dumps(ro["audit"]))


def save_recommendation(ro: dict):
    """Upsert one RO (idempotent on recommendation_id). Cross-DB single-row path."""
    ph = ",".join("?" * len(_COLS))
    db.execute_db(
        f"INSERT INTO recommendations ({','.join(_COLS)}) VALUES ({ph}) "
        f"ON CONFLICT(recommendation_id) DO UPDATE SET {_UPDATE}",
        _to_row(ro))


def save_recommendations_batch(ros: list) -> int:
    """Batched, transaction-safe upsert (O2). Deterministic order by recommendation_id.

    PostgreSQL: one execute_values per chunk inside an explicit transaction (commit on
    success, rollback on error → rollback-safe). SQLite: single-row fallback (identical
    semantics via the same ON CONFLICT). Returns rows attempted.
    """
    if not ros:
        return 0
    ros = sorted(ros, key=lambda r: r["meta"]["recommendation_id"])   # deterministic ordering
    if not db.is_postgresql():
        for ro in ros:
            save_recommendation(ro)
        return len(ros)

    from psycopg2.extras import execute_values
    pool = db._get_pg_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        rows = [_to_row(ro) for ro in ros]
        sql = (f"INSERT INTO recommendations ({','.join(_COLS)}) VALUES %s "
               f"ON CONFLICT(recommendation_id) DO UPDATE SET {_UPDATE}")
        for i in range(0, len(rows), 200):
            execute_values(cur, sql, rows[i:i + 200], page_size=200)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.autocommit = True      # restore pooled-conn default before returning it
        except Exception:
            pass
        pool.putconn(conn)


def get_deep_symbols_today(today: str) -> set:
    """Symbols with a same-day DEEP analysis — the supersession authority (O1).

    In P1 the deep-scan path (the auto-scan deep enrichment) writes only to
    scan_results_v2 and does NOT build ROs, so the authoritative deep-set is read from
    scan_results_v2 — exactly mirroring the save_results staleness guard (db.py:3706/3711).
    The RO pipeline OWNS the supersession DECISION; the deep-set SOURCE remains
    scan_results_v2 until the deep path is RO-ified (P2+), after which this can read the RO
    store (the `scan_mode` column) directly. `today` = the generated_at_utc date (YYYY-MM-DD).
    """
    try:
        if db.is_postgresql():
            rows = db.execute_db(
                "SELECT DISTINCT symbol FROM scan_results_v2 "
                "WHERE scan_date=? AND (data->>'scan_mode')='deep'", (today,), fetch="all")
        else:
            rows = db.execute_db(
                "SELECT DISTINCT symbol FROM scan_results_v2 "
                "WHERE scan_date=? AND json_extract(data,'$.scan_mode')='deep'", (today,), fetch="all")
        return {r["symbol"] for r in (rows or [])}
    except Exception:
        return set()   # fail-open: no cross-scan supersession this run (in-batch still applies)


def get_recommendation(symbol: str):
    """Latest RO for a symbol (the future SSOT read path)."""
    row = db.execute_db(
        "SELECT core, payloads, audit, status, scan_id, generated_at_utc "
        "FROM recommendations WHERE symbol=? ORDER BY generated_at_utc DESC LIMIT 1",
        (symbol.upper(),), fetch="one")
    return _row_to_ro(row)


def get_recommendations(scan_id: str):
    rows = db.execute_db(
        "SELECT core, payloads, audit, status, scan_id, generated_at_utc "
        "FROM recommendations WHERE scan_id=?", (scan_id,), fetch="all")
    return [_row_to_ro(r) for r in (rows or [])]


def _row_to_ro(row):
    if not row:
        return None
    core = json.loads(row["core"]) if row.get("core") else {}
    return {**core, "payloads": json.loads(row["payloads"]) if row.get("payloads") else None,
            "audit": json.loads(row["audit"]) if row.get("audit") else None,
            "status": row.get("status"), "scan_id": row.get("scan_id"),
            "generated_at_utc": row.get("generated_at_utc")}


def update_live_state(recommendation_id: str, **fields):
    """Single-writer live_state update (full impl in P3). P0/P1 stub: persists the overlay."""
    if not fields:
        return
    cols = ",".join(f"{k}=?" for k in fields)
    db.execute_db(
        f"INSERT INTO recommendation_live_state (recommendation_id, {','.join(fields)}) "
        f"VALUES (?{',?' * len(fields)}) "
        f"ON CONFLICT(recommendation_id) DO UPDATE SET {cols}, updated_at=CURRENT_TIMESTAMP",
        (recommendation_id, *fields.values(), *fields.values()))
