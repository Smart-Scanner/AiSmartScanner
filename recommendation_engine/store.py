"""RE-3 Recommendation store (RE-2A §4) — the single canonical RO read/write path.

Tables (additive, idempotent, cross-DB via db.execute_db):
  * recommendations          — immutable, versioned RO core + payloads + audit
  * recommendation_live_state — single-writer mutable runtime overlay (RE-1G §5)

P0 only persists; no consumer reads these yet.
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


def init_recommendation_store():
    """Idempotent table creation. Safe to call repeatedly."""
    db.execute_db(_DDL_REC)
    db.execute_db(_DDL_LIVE)
    db.execute_db("CREATE INDEX IF NOT EXISTS idx_rec_symbol_status ON recommendations(symbol, status)")
    db.execute_db("CREATE INDEX IF NOT EXISTS idx_rec_scan ON recommendations(scan_id)")


def save_recommendation(ro: dict):
    """Upsert one RO (idempotent on recommendation_id)."""
    m = ro["meta"]
    core = {k: ro[k] for k in ("engines", "scoring", "eligibility", "trade",
                               "sizing", "allocation", "presentation", "inputs_snapshot")}
    db.execute_db(
        """
        INSERT INTO recommendations
          (recommendation_id, symbol, exchange, scan_id, schema_version, model_version,
           formula_versions, input_hash, generated_at_utc, ttl_sec, status, eligible,
           supersedes_id, core, payloads, audit)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(recommendation_id) DO UPDATE SET
           status=excluded.status, eligible=excluded.eligible, core=excluded.core,
           payloads=excluded.payloads, audit=excluded.audit,
           generated_at_utc=excluded.generated_at_utc, model_version=excluded.model_version
        """,
        (m["recommendation_id"], m["symbol"], m.get("exchange", "NSE"), m["scan_id"],
         m["schema_version"], m["model_version"], json.dumps(ro["audit"]["formula_versions"]),
         m.get("input_hash") or ro["inputs_snapshot"].get("input_hash"), m["generated_at_utc"],
         m.get("ttl_sec", 86400), m["status"], 1 if ro["eligibility"]["eligible"] else 0,
         m.get("supersedes_id"), json.dumps(core), json.dumps(ro["payloads"]), json.dumps(ro["audit"])),
    )


def get_recommendation(symbol: str):
    """Latest RO for a symbol (single accessor — the future SSOT read path)."""
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
    """Single-writer live_state update (full impl in P3). P0 stub: persists the overlay."""
    if not fields:
        return
    cols = ",".join(f"{k}=?" for k in fields)
    db.execute_db(
        f"INSERT INTO recommendation_live_state (recommendation_id, {','.join(fields)}) "
        f"VALUES (?{',?' * len(fields)}) "
        f"ON CONFLICT(recommendation_id) DO UPDATE SET {cols}, updated_at=CURRENT_TIMESTAMP",
        (recommendation_id, *fields.values(), *fields.values()))
