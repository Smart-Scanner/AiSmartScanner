import os
from dotenv import load_dotenv

load_dotenv('.env')
import db
db.init_db()

db.execute_db("UPDATE scan_runs SET status = 'COMPLETED' WHERE status = 'RUNNING'")
db.execute_db("UPDATE scan_runs SET end_time = CURRENT_TIMESTAMP WHERE end_time IS NULL")
print("Lock cleared")
