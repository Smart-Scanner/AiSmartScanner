import os
import sys
import csv
import logging
from datetime import datetime

# Add root folder to sys.path so we can import modules
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

import db
from target_utils import resolve_targets

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("audit_script")

def run_audit():
    log.info("Starting Target Resolution Audit...")
    try:
        # Load all scanned stocks
        results = db.load_results(limit=9999, slim=True)
    except Exception as e:
        log.error("Failed to load results: %s", e)
        return

    total_symbols = len(results)
    matches = 0
    mismatches = 0
    missing = 0

    # ── Resolver Source Distribution counters ──
    src_trade = 0
    src_scan = 0
    src_missing = 0
    RESOLVED_FIELDS = ("t1", "t2", "t3", "sl", "entry_low", "entry_high")

    for s in results:
        sym = s.get("symbol", "?")
        resolved = resolve_targets(s, symbol=sym)

        # ── Resolver Source Distribution (per-field) ──
        sources = resolved.get("source", {})
        for field in RESOLVED_FIELDS:
            src = sources.get(field)
            if src is None:
                src_missing += 1
            elif src.startswith("trade."):
                src_trade += 1
            else:
                src_scan += 1

        # Check missing fields — skip signal comparison for incomplete records
        if not resolved.get("critical_fields_complete", False):
            missing += 1
            continue

        # Calculate signal match/mismatch
        cmp = float(s.get("price") or s.get("close") or 0.0)
        legacy_sl = float(s.get("stop_loss") or 0.0)
        legacy_target = float(s.get("target_price") or 0.0)

        resolved_sl = resolved.get("sl")
        resolved_t1 = resolved.get("t1")

        # ── Legacy signal ──
        legacy_signal = "HOLD"
        if legacy_sl > 0 and cmp <= legacy_sl:
            legacy_signal = "SELL"
        elif legacy_target > 0 and cmp >= legacy_target:
            legacy_signal = "BOOK PROFIT"

        # ── New signal ──
        new_signal = "HOLD"
        if resolved_sl is not None and cmp <= resolved_sl:
            new_signal = "SELL"
        elif resolved_t1 is not None and cmp >= resolved_t1:
            new_signal = "BOOK PROFIT"

        if legacy_signal == new_signal:
            matches += 1
        else:
            mismatches += 1

    # ── Coverage % ──
    coverage_pct = round(((total_symbols - missing) / total_symbols) * 100, 2) if total_symbols > 0 else 0.0

    # ── Resolver Source % ──
    total_fields = src_trade + src_scan + src_missing
    trade_pct = round((src_trade / total_fields) * 100, 2) if total_fields > 0 else 0.0
    scan_pct = round((src_scan / total_fields) * 100, 2) if total_fields > 0 else 0.0
    missing_field_pct = round((src_missing / total_fields) * 100, 2) if total_fields > 0 else 0.0

    # ── Invariant check ──
    invariant_ok = (matches + mismatches + missing) == total_symbols
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log.info("Audit Summary - Date: %s | Total: %d | Matches: %d | Mismatches: %d | Missing: %d | Coverage: %.2f%%",
             date_str, total_symbols, matches, mismatches, missing, coverage_pct)
    log.info("Resolver Sources - Trade: %.2f%% (%d) | Scan: %.2f%% (%d) | Missing: %.2f%% (%d) | Total Fields: %d",
             trade_pct, src_trade, scan_pct, src_scan, missing_field_pct, src_missing, total_fields)
    log.info("Invariant Check: Matches(%d) + Mismatches(%d) + Missing(%d) = %d == Total(%d) → %s",
             matches, mismatches, missing, matches + mismatches + missing, total_symbols,
             "✅ PASS" if invariant_ok else "❌ FAIL")

    # Append to csv log for historical trend
    csv_file = "audit_historical_trend.csv"
    file_exists = os.path.exists(csv_file)
    try:
        with open(csv_file, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Date", "Total Symbols", "Matches", "Mismatches", "Missing",
                                 "CoveragePct", "TradeSourcePct", "ScanSourcePct", "MissingFieldPct"])
            writer.writerow([date_str, total_symbols, matches, mismatches, missing,
                             coverage_pct, trade_pct, scan_pct, missing_field_pct])
        log.info("Saved audit trend results to %s", csv_file)
    except Exception as e:
        log.error("Failed to write to %s: %s", csv_file, e)

if __name__ == "__main__":
    run_audit()
