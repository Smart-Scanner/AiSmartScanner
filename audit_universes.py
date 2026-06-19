import db

db.init_db()

print("\n--- STATE ---")
resume = db.get_pending_resume()
active_version = db.get_meta("active_universe_version")

print(f"Resume state: {resume}")
print(f"Active version: {active_version}")

def get_count(version):
    if not version: return "N/A"
    try:
        return db.execute_db("SELECT COUNT(*) as c FROM eligible_universe WHERE universe_version=?", (version,), fetch="one")["c"]
    except Exception as e:
        return f"Error: {e}"

if resume:
    print(f"Resume Version {resume.get('universe_version')} count:", get_count(resume.get("universe_version")))
print(f"Active Version {active_version} count:", get_count(active_version))

tot = db.execute_db("SELECT COUNT(*) as c FROM eligible_universe", fetch="one")["c"]
print(f"Total eligible_universe table count: {tot}")
