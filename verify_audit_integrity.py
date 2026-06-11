"""
Audit Integrity Verification Script — Phase 6, Section 40.

Verifies the SHA256 hash-chain integrity of the scan_state_transitions table.
Detects:
  - Deleted rows (gaps in ID sequence)
  - Modified rows (hash mismatch)
  - Inserted/out-of-order rows (chain breaks)

Usage:
    python verify_audit_integrity.py
    python verify_audit_integrity.py --db path/to/smart_screener.db
"""

import sys
import os
import hashlib
import argparse

sys.path.insert(0, os.path.dirname(__file__))


def verify_chain(db_path: str = None, verbose: bool = False) -> dict:
    """Verify the hash-chain integrity of scan_state_transitions.

    Returns:
        {
            "total_rows": int,
            "verified": int,
            "broken_at": list[int],  # IDs where chain breaks detected
            "missing_ids": list[int],  # ID gaps (potential deletions)
            "null_hashes": int,  # Rows without hash_chain (legacy)
            "integrity_ok": bool,
        }
    """
    import sqlite3

    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), "smart_screener.db")

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}")
        return {"integrity_ok": False, "error": "db_not_found"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Check if the table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_state_transitions'"
    )
    if not cursor.fetchone():
        print("ERROR: scan_state_transitions table does not exist")
        conn.close()
        return {"integrity_ok": False, "error": "table_not_found"}

    # Fetch all rows ordered by ID (append-only assumption)
    cursor.execute("""
        SELECT id, scan_id, old_state, new_state, reason, actor,
               correlation_id, hash_chain, created_at
        FROM scan_state_transitions
        ORDER BY id ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        print("No transition rows found — nothing to verify.")
        return {
            "total_rows": 0, "verified": 0, "broken_at": [],
            "missing_ids": [], "null_hashes": 0, "integrity_ok": True,
        }

    verified = 0
    broken_at = []
    missing_ids = []
    null_hashes = 0
    prev_hash = ""

    for i, row in enumerate(rows):
        row_id = row["id"]

        # Check for ID gaps (potential deletion)
        if i > 0:
            expected_id = rows[i - 1]["id"] + 1
            if row_id != expected_id:
                gap_range = list(range(expected_id, row_id))
                missing_ids.extend(gap_range)
                if verbose:
                    print(f"  ⚠ ID GAP: expected {expected_id}, got {row_id} "
                          f"(missing IDs: {gap_range})")

        stored_hash = row["hash_chain"]

        # Skip rows without hash_chain (legacy pre-Phase-6 rows)
        if not stored_hash:
            null_hashes += 1
            prev_hash = ""  # Reset chain for next segment
            if verbose:
                print(f"  ○ ID {row_id}: no hash_chain (legacy row)")
            continue

        # Recompute the hash
        chain_input = "|".join([
            prev_hash,
            row["scan_id"] or "",
            row["old_state"] or "",
            row["new_state"] or "",
            row["reason"] or "",
            row["actor"] or "",
            row["correlation_id"] or "",
            row["created_at"] or "",
        ])
        computed_hash = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

        if computed_hash == stored_hash:
            verified += 1
            if verbose:
                print(f"  ✓ ID {row_id}: hash OK")
        else:
            broken_at.append(row_id)
            if verbose:
                print(f"  ✗ ID {row_id}: HASH MISMATCH!")
                print(f"    Stored:   {stored_hash[:16]}...")
                print(f"    Computed: {computed_hash[:16]}...")
                print(f"    Input:    {chain_input[:80]}...")

        prev_hash = stored_hash

    result = {
        "total_rows": total,
        "verified": verified,
        "broken_at": broken_at,
        "missing_ids": missing_ids,
        "null_hashes": null_hashes,
        "integrity_ok": len(broken_at) == 0 and len(missing_ids) == 0,
    }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Verify scan_state_transitions hash-chain integrity"
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to SQLite database (default: smart_screener.db)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-row verification details"
    )
    args = parser.parse_args()

    print("═" * 60)
    print("  Audit Integrity Verification — Phase 6, Section 40")
    print("═" * 60)
    print()

    result = verify_chain(db_path=args.db, verbose=args.verbose)

    if "error" in result:
        print(f"\nFATAL: {result['error']}")
        sys.exit(2)

    print()
    print(f"Total rows:        {result['total_rows']}")
    print(f"Verified (OK):     {result['verified']}")
    print(f"Legacy (no hash):  {result['null_hashes']}")
    print(f"Chain breaks:      {len(result['broken_at'])}")
    print(f"Missing IDs:       {len(result['missing_ids'])}")
    print()

    if result["integrity_ok"]:
        print("✅ AUDIT CHAIN INTEGRITY: PASS")
        print("   No tampering detected. All hash-chain links verified.")
    else:
        print("❌ AUDIT CHAIN INTEGRITY: FAIL")
        if result["broken_at"]:
            print(f"   Hash mismatches at IDs: {result['broken_at']}")
            print("   → Possible unauthorized modification of transition records.")
        if result["missing_ids"]:
            print(f"   Missing IDs (possible deletions): {result['missing_ids']}")
            print("   → Possible unauthorized deletion of transition records.")

    print("═" * 60)
    sys.exit(0 if result["integrity_ok"] else 1)


if __name__ == "__main__":
    main()
