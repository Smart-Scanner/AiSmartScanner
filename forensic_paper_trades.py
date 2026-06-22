import os
import sys
from datetime import datetime

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

import db

def run_forensics():
    # 1. Fetch all open trades
    trades = db.execute_db("SELECT * FROM paper_trades WHERE status = 'OPEN'", fetch="all")
    print(f"Found {len(trades)} open paper trades.")
    
    if not trades:
        return
        
    print(f"{'Trade ID':<10} | {'Symbol':<10} | {'Created At':<25} | {'Entry Date':<12} | {'Order ID':<38} | {'Latency':<8} | {'Origin Source':<20}")
    print("-" * 135)
    
    for t in trades:
        trade_id = t.get("id")
        symbol = t.get("symbol")
        created_at = t.get("created_at")
        entry_date = t.get("entry_date")
        
        # New columns added in execution engine phase
        order_id = t.get("order_id")
        latency = t.get("execution_latency_ms")
        
        # Determine origin
        origin = "Unknown"
        
        if order_id:
            # Check paper_orders
            order = db.execute_db("SELECT * FROM paper_orders WHERE order_id = ?", (order_id,), fetch="one")
            if order:
                origin = f"ExecutionEngine (Order {order_id[:8]})"
            else:
                origin = "ExecutionEngine (Orphaned Order)"
        elif latency is not None:
            origin = "ExecutionEngine (Direct Fill)"
        else:
            # Check recommendation_snapshots
            rec = db.execute_db("""
                SELECT * FROM recommendation_snapshots 
                WHERE symbol = ? AND DATE(created_at) = ?
                ORDER BY created_at DESC LIMIT 1
            """, (symbol, str(created_at)[:10]), fetch="one")
            
            if rec:
                origin = "Scanner Auto-Create (from snapshot)"
            else:
                # Check legacy scan_results
                res = db.execute_db("""
                    SELECT * FROM scan_results 
                    WHERE symbol = ? AND DATE(created_at) = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (symbol, str(created_at)[:10]), fetch="one")
                if res:
                    origin = "Scanner Auto-Create (from scan_results)"
                else:
                    # Check if it matches seed/test dates
                    if str(entry_date) == "2026-06-10" or str(entry_date) == "2024-05-15":
                        origin = "Test Harness / Seed Data"
                    else:
                        origin = "Manual / Uncorrelated Insert"
        
        print(f"{str(trade_id):<10} | {symbol:<10} | {str(created_at):<25} | {str(entry_date):<12} | {str(order_id or 'NULL'):<38} | {str(latency or 'NULL'):<8} | {origin}")

if __name__ == '__main__':
    run_forensics()
