import os
import time
import json
import socket
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

import pyotp
from SmartApi import SmartConnect

# Prevent infinite socket hangs
socket.setdefaulttimeout(10.0)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class MockProvider:
    def __init__(self, key_prefix):
        self.name = key_prefix
        self.api_key = os.getenv(f"{key_prefix}_API_KEY", "")
        self.client_id = os.getenv(f"{key_prefix}_CLIENT_ID", "")
        self.mpin = os.getenv(f"{key_prefix}_MPIN", "")
        self.totp_secret = os.getenv(f"{key_prefix}_TOTP", "")
        self.api = None
        self.success = 0
        self.fail_429 = 0
        self.fail_other = 0
        self.latencies = []

    def login(self):
        self.api = SmartConnect(api_key=self.api_key)
        totp = pyotp.TOTP(self.totp_secret).now()
        res = self.api.generateSession(self.client_id, self.mpin, totp)
        return res.get("status", False)

    def fetch(self, token, exchange="NSE"):
        start = time.time()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": "ONE_DAY",
                "fromdate": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                "todate": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            res = self.api.getCandleData(params)
            duration = time.time() - start
            
            if res and res.get("status"):
                self.success += 1
                self.latencies.append(duration)
            elif res and res.get("errorcode") == "AB1019":
                self.fail_429 += 1
            else:
                self.fail_other += 1
        except Exception as e:
            msg = str(e)
            if "Access denied" in msg or "access rate" in msg:
                self.fail_429 += 1
            else:
                self.fail_other += 1

def run_optimization():
    p1 = MockProvider("PROVIDER_1")
    if not p1.login():
        logging.error("P1 failed to login.")
        return
        
    try:
        with open("cache/angel_tokens.json", "r") as f:
            all_tokens = list(json.load(f).values())
    except:
        logging.error("No tokens found.")
        return

    gaps_to_test = [0.6, 0.8, 1.0, 1.2]
    symbols_per_test = 100
    
    results = {}
    
    for idx, gap in enumerate(gaps_to_test):
        logging.info(f"=== TESTING GAP: {gap}s ===")
        
        # Reset stats
        p1.success = p1.fail_429 = p1.fail_other = 0
        p1.latencies = []
        
        tokens_slice = all_tokens[idx*symbols_per_test : (idx+1)*symbols_per_test]
        
        start_time = time.time()
        for i, t in enumerate(tokens_slice):
            p1.fetch(t)
            time.sleep(gap)
        
        duration = time.time() - start_time
        
        rate_429 = (p1.fail_429 / symbols_per_test) * 100
        avg_lat = sum(p1.latencies) / len(p1.latencies) if p1.latencies else 0
        
        results[gap] = {
            "duration": duration,
            "success": p1.success,
            "429s": p1.fail_429,
            "429_rate": rate_429,
            "avg_latency": avg_lat
        }
        
        logging.info(f"GAP {gap}s -> Success: {p1.success}/{symbols_per_test} | 429s: {p1.fail_429} ({rate_429:.1f}%) | Duration: {duration:.1f}s")
        
        logging.info("Cooling down for 10 seconds before next gap test...")
        time.sleep(10)

    logging.info("\n=== FINAL OPTIMIZATION RESULTS ===")
    for gap, res in results.items():
        logging.info(f"Gap {gap}s:")
        logging.info(f"  - 429 Rate: {res['429_rate']:.1f}%")
        logging.info(f"  - Total Time: {res['duration']:.1f}s")
        logging.info(f"  - Avg API Latency: {res['avg_latency']*1000:.0f}ms")
        logging.info("--------------------")

if __name__ == "__main__":
    run_optimization()
