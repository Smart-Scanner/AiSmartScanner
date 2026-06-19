import db
import json

db.init_db()
print("CURRENT SCAN STATE SCHEMA:")
try:
    print(db.execute_db("SELECT sql FROM sqlite_master WHERE name='current_scan_state'", fetch="one")["sql"])
except Exception as e: print(e)

print("\nSCAN RESUME STATE SCHEMA:")
try:
    print(db.execute_db("SELECT sql FROM sqlite_master WHERE name='scan_resume_state'", fetch="one")["sql"])
except Exception as e: print(e)

print("\nSCAN RUNS SCHEMA:")
try:
    print(db.execute_db("SELECT sql FROM sqlite_master WHERE name='scan_runs'", fetch="one")["sql"])
except Exception as e: print(e)
