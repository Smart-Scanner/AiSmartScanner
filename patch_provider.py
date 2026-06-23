import re

def patch_provider():
    with open("data_provider.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update fetch_historical signature and role check
    old_fetch = """    def fetch_historical(self, symboltoken: str, exchange: str = "NSE") -> Optional[list]:
        if self.role == "EXECUTION":
            raise RuntimeError(f"[{self.name}] FATAL: Cannot fetch historical data using an EXECUTION provider!")"""
    
    new_fetch = """    def fetch_historical(self, symboltoken: str, exchange: str = "NSE", fromdate: str = None, todate: str = None, interval: str = "ONE_DAY") -> Optional[list]:
        if self.role == "EXECUTION":
            msg = f"[{self.name}] FATAL: Cannot fetch historical data using an EXECUTION provider!"
            logging.critical(f"[ROLE_VIOLATION] {msg}")
            try:
                from db import audit_log
                audit_log("ROLE_VIOLATION", f"Provider {self.name}", f"historical_fetch for {symboltoken}")
            except Exception:
                pass
            raise RuntimeError(msg)"""
    
    content = content.replace(old_fetch, new_fetch)

    # 2. Update return self._do_fetch
    content = content.replace("return self._do_fetch(symboltoken, exchange)", "return self._do_fetch(symboltoken, exchange, fromdate, todate, interval)")

    # 3. Update _do_fetch signatures
    old_do_fetch_base = "def _do_fetch(self, symboltoken: str, exchange: str) -> Optional[list]:"
    new_do_fetch_base = "def _do_fetch(self, symboltoken: str, exchange: str, fromdate: str = None, todate: str = None, interval: str = \"ONE_DAY\") -> Optional[list]:"
    content = content.replace(old_do_fetch_base, new_do_fetch_base)

    # 4. Update AngelProvider _do_fetch logic
    old_angel_fetch = """    def _do_fetch(self, symboltoken: str, exchange: str, fromdate: str = None, todate: str = None, interval: str = "ONE_DAY") -> Optional[list]:
        start_time = time.time()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": "ONE_DAY",
                "fromdate": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                "todate": datetime.now().strftime("%Y-%m-%d %H:%M")
            }"""
    
    new_angel_fetch = """    def _do_fetch(self, symboltoken: str, exchange: str, fromdate: str = None, todate: str = None, interval: str = "ONE_DAY") -> Optional[list]:
        start_time = time.time()
        try:
            if not fromdate:
                fromdate = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
            if not todate:
                todate = datetime.now().strftime("%Y-%m-%d %H:%M")
            params = {
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": interval,
                "fromdate": fromdate,
                "todate": todate
            }"""
    
    content = content.replace(old_angel_fetch, new_angel_fetch)

    with open("data_provider.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_provider()
    print("Patched data_provider.py successfully.")
