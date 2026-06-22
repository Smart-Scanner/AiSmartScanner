import db
import time
import json
from datetime import datetime

def fast_mock_scan():
    # Simulate scanner setup
    scan_id = "scan_manual_999_fast"
    universe_version = db.get_meta("active_universe_version") or "UNIVERSE_v014"
    
    # Lock version
    db.execute_db("""
        INSERT INTO scan_runs (scan_id, mode, status, universe_version, candidate_count)
        VALUES (?, 'manual', 'running', ?, 575)
    """, (scan_id, universe_version))
    
    # Get universe
    eligible_rows = db.get_eligible_universe(universe_version)
    symbols = [r["symbol"] for r in eligible_rows]
    
    # If 0 symbols (maybe UNIVERSE_v014 has no symbols in this env), fallback to generating 575
    if len(symbols) == 0:
        symbols = [f"MOCK_{i}" for i in range(575)]
        
    print(f"Mock scanning {len(symbols)} symbols...")
    
    # Create mock results
    results = []
    for sym in symbols:
        results.append({
            "symbol": sym,
            "LTP": 100.0,
            "rsi": 50,
            "technical_score": 60,
            "fundamental_score": 50,
            "total_score": 110,
            "high_conviction": True,
            "sector": "Mock Sector"
        })
        
    # Test the EXACT db.save_results path
    db.save_results(results, scan_id)
    
    # Complete scan
    db.execute_db("""
        UPDATE scan_runs SET status = 'COMPLETED', end_time = CURRENT_TIMESTAMP, processed_count = ?
        WHERE scan_id = ?
    """, (len(results), scan_id))
    
    print("Mock scan complete.")
    
if __name__ == "__main__":
    fast_mock_scan()
