#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, r"c:\Users\91971\Downloads\smart-screener-deploy")

import db
from universe import get_active_universe

def main():
    print("=== UNIVERSE BOOTSTRAP (B-PRIME) ===")
    
    # 1. Fetch current 574 fallback stocks
    symbols = get_active_universe()
    count = len(symbols)
    print(f"Loaded {count} stocks from fallback universe.")
    
    version = "UNIVERSE_v001_BOOTSTRAP"
    
    # 2. Populate eligible_universe
    data = []
    for s in symbols:
        data.append({
            "symbol": s,
            "market_cap_cr": 0,
            "avg_volume_20d": 0,
            "avg_turnover_20d": 0,
            "price": 0,
            "eligibility_reason": "BOOTSTRAP_FALLBACK"
        })
    db.save_eligible_universe(data, version)
    print(f"Created bootstrap snapshot {version} in eligible_universe.")
    
    # 3. Create validation snapshot
    # Schema: universe_version, candidate_count, eligible_count, marketcap_coverage_pct, liquidity_coverage_pct
    db.execute_db("""
        INSERT INTO universe_build_validation_snapshot 
        (universe_version, candidate_count, eligible_count, marketcap_coverage_pct, liquidity_coverage_pct)
        VALUES (?, ?, ?, 0, 0)
    """, (version, count, count))
    print("Created validation snapshot.")
    
    # 4. Populate active_universe_version metadata
    db.set_meta("active_universe_version", version)
    print(f"Set active_universe_version = {version}")
    print("Bootstrap complete.")

if __name__ == "__main__":
    main()
