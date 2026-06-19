#!/usr/bin/env python3
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db

def main():
    print("=== P0.1C RISK FACTOR FORENSICS ===\n")
    
    print("--- Risk 1: active_universe_version governance ---")
    active_v = db.get_meta("active_universe_version")
    print(f"Current active_universe_version: {active_v}")
    print("Let's see if there are any build snapshots in the DB...")
    schema = db.execute_db("SELECT sql FROM sqlite_master WHERE type='table' AND name='universe_build_validation_snapshot'", fetch="one")
    print(f"Table schema: {schema['sql'] if schema else 'Not found'}")
    versions = db.execute_db("SELECT * FROM universe_build_validation_snapshot ORDER BY build_timestamp DESC LIMIT 5", fetch="all")
    print("Latest 5 versions:")
    for v in versions:
        print(f"  {v}")
        
    print("\n--- Risk 2: scan_runs error message preservation ---")
    print("Let's query the failed scan_runs and see how many have error_message IS NULL")
    null_err = db.execute_db("SELECT COUNT(*) as cnt FROM scan_runs WHERE status='failed' AND error_message IS NULL", fetch="one")
    print(f"Failed scans with NULL error_message: {null_err['cnt']}")
    zombie = db.execute_db("SELECT scan_id, status, error_message FROM scan_runs WHERE scan_id='scan_auto_1781622574_c8ed0c'", fetch="one")
    print(f"Zombie scan details: {zombie}")
    
    print("\n--- Risk 3: Universe count consistency ---")
    cat_count = db.execute_db("SELECT COUNT(*) as cnt FROM universe_catalog", fetch="one")
    elig_count = db.execute_db("SELECT COUNT(*) as cnt FROM universe_catalog WHERE is_eligible=1", fetch="one")
    print(f"Total symbols in catalog: {cat_count['cnt']}")
    print(f"Eligible symbols in catalog: {elig_count['cnt']}")
    

if __name__ == "__main__":
    main()
