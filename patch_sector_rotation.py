import re

def patch_sector_rotation():
    with open("intelligence/sector_rotation.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update imports
    old_imports = "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_download"
    new_imports = "from data_provider.historical_service import get_daily_history"
    # Note: I put historical_service in the root (actually no, I put it in the root so it's `import historical_service`)
    new_imports = "from historical_service import get_daily_history\nimport pandas as pd"
    content = content.replace(old_imports, new_imports)

    # 2. Hardcode ANGEL_INDEX_TOKENS and BENCHMARK
    old_benchmark = 'BENCHMARK = "^NSEI"'
    new_benchmark = 'BENCHMARK = "26000"  # Nifty 50'
    content = content.replace(old_benchmark, new_benchmark)

    old_nifty_sectors = """NIFTY_SECTORS = {
    "Bank Nifty":    "^NSEBANK",
    "Nifty IT":      "^CNXIT",
    "Nifty Pharma":  "^CNXPHARMA",
    "Nifty Auto":    "^CNXAUTO",
    "Nifty FMCG":    "^CNXFMCG",
    "Nifty Metal":   "^CNXMETAL",
    "Nifty Realty":  "^CNXREALTY",
    "Nifty Energy":  "^CNXENERGY",
    "Nifty Infra":   "^CNXINFRA",
    "Nifty PSU":     "^CNXPSUBANK",
    "Nifty Media":   "^CNXMEDIA",
    "Nifty Midcap":  "^NSEMDCP50",
}"""

    new_nifty_sectors = """NIFTY_SECTORS = {
    "Bank Nifty":    "26009",
    "Nifty IT":      "26024",
    "Nifty Pharma":  "26012",
    "Nifty Auto":    "26014",
    "Nifty FMCG":    "26007",
    "Nifty Metal":   "26011",
    "Nifty Realty":  "26013",
    "Nifty Energy":  "26006",
    "Nifty Infra":   "26005",
    "Nifty PSU":     "26020",
    "Nifty Media":   "26003",
    "Nifty Midcap":  "26025",
}"""
    content = content.replace(old_nifty_sectors, new_nifty_sectors)

    # 3. Replace the `get_yf_download` in `scan_sector_rotation` with `get_daily_history`
    old_scan_loop = """    try:
        bench_df = get_yf_download(BENCHMARK, source="sector_rotation", period="6mo", interval="1d",
                               progress=False, auto_adjust=True)
        if bench_df.empty or len(bench_df) < 20:
            log.warning("Benchmark data empty, skipping RRG")
            _rrg_running = False
            return
        bench = bench_df["Close"].squeeze()

        for name, ticker in NIFTY_SECTORS.items():
            try:
                sec_df = get_yf_download(ticker, source="sector_rotation", period="6mo", interval="1d",
                                     progress=False, auto_adjust=True)
                if sec_df.empty or len(sec_df) < 20:
                    continue
                sec = sec_df["Close"].squeeze()"""
    
    new_scan_loop = """    try:
        def _fetch_to_series(token):
            data = get_daily_history(token, days=180, exchange="NSE")
            if not data: return pd.Series(dtype=float)
            df = pd.DataFrame(data, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            return df["Close"].squeeze()

        bench = _fetch_to_series(BENCHMARK)
        if bench.empty or len(bench) < 20:
            log.warning("Benchmark data empty, skipping RRG")
            _rrg_running = False
            return

        for name, ticker in NIFTY_SECTORS.items():
            try:
                sec = _fetch_to_series(ticker)
                if sec.empty or len(sec) < 20:
                    continue"""
    content = content.replace(old_scan_loop, new_scan_loop)

    with open("intelligence/sector_rotation.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_sector_rotation()
    print("Patched sector_rotation.py successfully.")
