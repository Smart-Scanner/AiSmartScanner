import os
import sys
from dotenv import load_dotenv

# Enforce GATE_LIMIT 574 for safe dry-run
os.environ["FULL_UNIVERSE"] = "1"
os.environ["GATE_LIMIT"] = "574"

load_dotenv()

import db
import scanner
from scan_context import ScanContext

print("Starting Gated Dry-Run Scan (Phase C)...")
print("FULL_UNIVERSE:", os.environ["FULL_UNIVERSE"])
print("GATE_LIMIT:", os.environ["GATE_LIMIT"])

# Force pool initialization
db.init_db()

# Create a dedicated context
context = ScanContext.create(
    trigger_source="manual",
    user_id="system",
    mode="manual"
)

print(f"Scan ID: {context.scan_id}")

try:
    # Run the scan
    scanner.run_full_scan(context)
    print("Scan completed successfully.")
except Exception as e:
    print(f"Scan failed: {e}")
