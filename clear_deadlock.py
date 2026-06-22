import db

print("Clearing scan resume states...")
try:
    db.execute_db("DELETE FROM scan_resume_state")
    print("Deleted corrupted resume states.")
except Exception as e:
    print(e)

try:
    db.execute_db("UPDATE current_scan_state SET status='idle', scan_id=NULL, cancel_requested=0 WHERE id=1")
    print("Cleared current scan lock.")
except Exception as e:
    print(e)

try:
    db.execute_db("UPDATE scan_runs SET status='aborted' WHERE status='running'")
    print("Aborted dangling runs.")
except Exception as e:
    print(e)

print("Deadlock cleared.")
