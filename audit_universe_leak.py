import os
import sys
import json
from datetime import datetime

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

import db

def run_audits():
    print("--- AUDIT 3: PRODUCTION EVIDENCE ---")
    scan = db.execute_db("SELECT * FROM scan_runs ORDER BY start_time DESC LIMIT 1", fetch="one")
    if scan:
        print(f"scan_id: {scan.get('scan_id')}")
        print(f"universe_version: {scan.get('universe_version')}")
        print(f"eligible_count: {scan.get('candidate_count')}")
        print(f"processed_count: {scan.get('processed_count')}")
        
        # Actual symbols scanned
        results = db.execute_db("SELECT symbol FROM scan_results ORDER BY symbol ASC", fetch="all")
        symbols = [r['symbol'] for r in results] if results else []
        print(f"Actual symbols generated in scan_results: {len(symbols)}")
        if len(symbols) >= 40:
            print(f"first 20 symbols: {symbols[:20]}")
            print(f"last 20 symbols: {symbols[-20:]}")
        
        # Look at scan_batches
        batches = db.execute_db("SELECT sum(symbol_count) as c FROM scan_batches WHERE scan_id=?", (scan.get('scan_id'),), fetch="one")
        print(f"Total symbols passed to scan_batches: {batches.get('c') if batches else 0}")
    else:
        print("No scan runs found.")

    print("\n--- AUDIT 4: FILTER CHAIN FORENSICS ---")
    history = db.execute_db("SELECT * FROM universe_rebuild_history ORDER BY id DESC LIMIT 1", fetch="one")
    if history:
        print(f"Raw Universe (input_count): {history.get('input_count')}")
        print(f"Market Cap Filter rejected: {history.get('rejected_mcap')}")
        print(f"Turnover Filter rejected: {history.get('rejected_turnover')}")
        print(f"Volume Filter rejected: {history.get('rejected_volume')}")
        print(f"Price Filter rejected: {history.get('rejected_price')}")
        print(f"ETF Filter rejected: {history.get('rejected_etf')}")
        print(f"SME Filter rejected: {history.get('rejected_sme')}")
        print(f"Suspended rejected: {history.get('rejected_suspended')}")
        print(f"Force included (Portfolio/Watchlist): {history.get('force_included')}")
        print(f"Eligible Universe (eligible_count): {history.get('eligible_count')}")
    else:
        print("No universe rebuild history found.")

    print("\n--- AUDIT 5: UNIVERSE LEAK DETECTION ---")
    if scan:
        # Check if any rejected symbols made it into the scan results
        etf_check = db.execute_db("""
            SELECT symbol FROM scan_results 
            WHERE symbol LIKE '%BEES' OR symbol LIKE '%ETF%' OR symbol LIKE '%LIQUID%'
        """, fetch="all")
        print(f"ETF/BEES symbols in scan_results: {[r['symbol'] for r in etf_check] if etf_check else 'None'}")
        
        sme_check = db.execute_db("""
            SELECT symbol FROM scan_results 
            WHERE symbol LIKE '%SME' OR symbol LIKE '%-SM'
        """, fetch="all")
        print(f"SME symbols in scan_results: {[r['symbol'] for r in sme_check] if sme_check else 'None'}")

if __name__ == '__main__':
    run_audits()
