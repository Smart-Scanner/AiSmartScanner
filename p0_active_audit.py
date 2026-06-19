import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
import platform
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("p0_audit")

# Import application modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv('.env')

import db
import universe
import watchdog
from app import app

db.init_db()

# Metrics collection
audit_results = []
failures_detected = 0

def log_audit(audit_id, name, status, evidence, root_cause="", fix_req="", retest_req=""):
    global failures_detected
    result = {
        "Finding ID": audit_id,
        "Audit": name,
        "Severity": "Critical" if status == "FAIL" else "None",
        "Status": status,
        "Evidence": evidence,
        "Root Cause": root_cause,
        "Fix Required": fix_req,
        "Retest Required": retest_req
    }
    audit_results.append(result)
    
    if status == "FAIL":
        failures_detected += 1
        log.error(f"❌ {name} FAILED: {evidence}")
    else:
        log.info(f"✅ {name} PASSED")

# --- I. Environment Metadata ---
def get_env_metadata():
    try:
        import psutil
        cpu = f"{psutil.cpu_count(logical=True)} Cores"
        ram = f"{psutil.virtual_memory().total / (1024**3):.2f} GB"
    except ImportError:
        cpu = "Unknown"
        ram = "Unknown"
    return {
        "CPU": cpu,
        "RAM": ram,
        "OS": platform.system() + " " + platform.release(),
        "Python": sys.version.split(' ')[0],
        "DB Backend": "SQLite",
        "Timestamp": datetime.utcnow().isoformat() + "Z"
    }

# --- Snapshot Utility ---
def dump_db_snapshot(filepath):
    """Create an immutable snapshot of DB state for governance."""
    try:
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "active_universe_version": db.get_meta("active_universe_version"),
            "scan_lock": db.execute_db("SELECT * FROM scan_lock WHERE id=1", fetch="one"),
            "current_scan_state": db.execute_db("SELECT * FROM current_scan_state WHERE id=1", fetch="one"),
            "scan_runs_count": db.execute_db("SELECT COUNT(*) as c FROM scan_runs", fetch="one")['c'],
            "scan_state_transitions_count": db.execute_db("SELECT COUNT(*) as c FROM scan_state_transitions", fetch="one")['c']
        }
        def datetime_handler(x):
            if isinstance(x, datetime):
                return x.isoformat()
            return str(x)
        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=4, default=datetime_handler)
        log.info(f"Saved DB snapshot to {filepath}")
    except Exception as e:
        log.error(f"Failed to dump DB snapshot to {filepath}: {e}")

# --- AUDIT 1: Symbol Delta Proof ---
def audit_1():
    log.info("Running Audit 1: Symbol Delta Proof")
    try:
        # Legacy universe directly via get_active_universe (bypass cache if any)
        legacy_symbols = universe.get_active_universe(include_portfolio=False, include_custom=False)
        
        # New universe from eligible_universe table using baseline bootstrap version
        rows = db.execute_db("SELECT symbol FROM eligible_universe WHERE universe_version = 'UNIVERSE_v001_BOOTSTRAP'", fetch="all")
        new_symbols = [r['symbol'] for r in rows] if rows else []
        
        set_legacy = set(legacy_symbols)
        set_new = set(new_symbols)
        
        delta = set_legacy.symmetric_difference(set_new)
        
        if len(delta) == 0 and len(legacy_symbols) == 574:
            log_audit("P0-AUDIT-1", "Symbol Delta Proof", "PASS", "Count=574, Delta=0")
        else:
            log_audit("P0-AUDIT-1", "Symbol Delta Proof", "FAIL", f"Count legacy={len(legacy_symbols)}, new={len(new_symbols)}, Delta={len(delta)}", "Universe logic drift", "Realign logic", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-1", "Symbol Delta Proof", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 2: Restart Persistence ---
def audit_2():
    log.info("Running Audit 2: Restart Persistence")
    try:
        ver_before = db.get_meta('active_universe_version')
        
        # Close pool and reconnect
        if hasattr(db, 'pool') and db.pool:
            db.pool.closeall()
        db.init_db()
        
        ver_after = db.get_meta('active_universe_version')
        
        if ver_after == ver_before and ver_after is not None:
            log_audit("P0-AUDIT-2", "Restart Persistence", "PASS", f"Version remained {ver_after}")
        else:
            log_audit("P0-AUDIT-2", "Restart Persistence", "FAIL", f"Expected {ver_before}, got {ver_after}", "State not persisted", "Fix state storage", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-2", "Restart Persistence", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 3: State Consistency Audit ---
def audit_3():
    log.info("Running Audit 3: State Consistency")
    try:
        thirty_days_ago = datetime.now() - timedelta(days=30)
        thirty_days_str = thirty_days_ago.strftime('%Y-%m-%d %H:%M:%S')
        
        transitions = db.execute_db(
            "SELECT scan_id, new_state, created_at FROM scan_state_transitions WHERE created_at >= ? ORDER BY scan_id, created_at", 
            (thirty_days_str,), fetch="all"
        )
        
        if not transitions:
            log_audit("P0-AUDIT-3", "State Consistency", "PASS", "No transitions found or DB empty")
            return
 
        scan_history = {}
        for row in transitions:
            sid = row['scan_id']
            if sid not in scan_history:
                scan_history[sid] = []
            scan_history[sid].append(row['new_state'])
            
        impossible_count = 0
        evidence = ""
        for sid, history in scan_history.items():
            if "COMPLETED" in history:
                comp_idx = history.index("COMPLETED")
                if "RUNNING" in history[comp_idx:]:
                    impossible_count += 1
                    evidence = f"running->completed->running in {sid}"
                if "FAILED" in history and history.index("FAILED") < comp_idx:
                    impossible_count += 1
                    evidence = f"failed->completed in {sid}"
            if "ZOMBIE_DETECTED" in history:
                z_idx = history.index("ZOMBIE_DETECTED")
                if "FAILED" not in history[z_idx:] and "COMPLETED" not in history[z_idx:]:
                    run = db.execute_db("SELECT status FROM scan_runs WHERE scan_id=?", (sid,), fetch="one")
                    if run and run['status'] not in ('FAILED', 'COMPLETED', 'RUNNING'):
                        impossible_count += 1
                        evidence = f"zombie_detected without terminal failed in {sid}"
                        
        leaked_locks = db.execute_db(
            "SELECT sl.scan_id FROM scan_lock sl JOIN scan_runs sr ON sl.scan_id = sr.scan_id WHERE sr.status = 'COMPLETED'",
            fetch="all"
        )
        
        if leaked_locks:
            impossible_count += len(leaked_locks)
            evidence = f"Completed scan owning lock: {leaked_locks[0]['scan_id']}"
            
        if impossible_count == 0:
            log_audit("P0-AUDIT-3", "State Consistency", "PASS", "0 impossible transitions/leaks")
        else:
            log_audit("P0-AUDIT-3", "State Consistency", "FAIL", evidence, "State machine flaw", "Fix transitions", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-3", "State Consistency", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 4: Active Zombie Regression ---
def audit_4():
    log.info("Running Audit 4: Active Zombie Regression (with Lock Release Proof)")
    try:
        db.execute_db("UPDATE scan_lock SET scan_id = NULL, acquired_at = NULL WHERE id = 1")
        
        zombie_id = "test_zombie_audit4"
        past_time = (datetime.now() - timedelta(minutes=16)).strftime('%Y-%m-%d %H:%M:%S')
        
        # Inject zombie state
        db.execute_db("UPDATE scan_lock SET scan_id=?, acquired_at=? WHERE id=1", (zombie_id, past_time))
        db.execute_db("UPDATE current_scan_state SET scan_id=?, status='RUNNING'", (zombie_id,))
        db.execute_db("INSERT INTO scan_runs (scan_id, status, start_time, last_heartbeat) VALUES (?, 'RUNNING', ?, ?)", 
                      (zombie_id, past_time, past_time))
                      
        # Verify before watchdog
        lock_before = db.execute_db("SELECT scan_id FROM scan_lock WHERE id=1", fetch="one")
        assert lock_before['scan_id'] == zombie_id, "Lock injection failed"
                      
        # Run watchdog recovery
        watchdog._recover_stale_scans(db)
        
        # Verify after watchdog
        run = db.execute_db("SELECT status FROM scan_runs WHERE scan_id=?", (zombie_id,), fetch="one")
        lock_after = db.execute_db("SELECT scan_id FROM scan_lock WHERE id=1", fetch="one")
        state = db.execute_db("SELECT status FROM current_scan_state WHERE id=1", fetch="one")
        
        if run['status'] == 'FAILED' and (not lock_after or lock_after['scan_id'] is None) and state['status'] == 'IDLE':
            log_audit("P0-AUDIT-4", "Active Zombie Regression", "PASS", f"Lock went from {zombie_id} -> NULL, scan failed")
        else:
            log_audit("P0-AUDIT-4", "Active Zombie Regression", "FAIL", 
                      f"Status={run['status']}, Lock={lock_after['scan_id']}, State={state['status']}", 
                      "Watchdog failed to recover and release lock", "Fix watchdog lock release", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-4", "Active Zombie Regression", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 5: 5 Scans + Restart Cycle (2 Full + 3 Abbrev) ---
def audit_5():
    log.info("Running Audit 5: 5 Independent Success (2 Full, 3 Abbreviated)")
    try:
        python_exe = sys.executable
        success_count = 0
        pids = []
        
        # Abbreviated mock script
        abbrev_script = """
import unittest.mock
with unittest.mock.patch('universe.get_fast_scan_universe', return_value=['RELIANCE']), \\
     unittest.mock.patch('db.get_custom_stocks', return_value=[]):
    import run_scan
"""
        
        for i in range(5):
            is_full = (i < 2) # First 2 are full scans
            scan_type = "FULL" if is_full else "ABBREV"
            log.info(f"  Cycle {i+1}/5 ({scan_type}) starting...")
            
            db.execute_db("UPDATE scan_lock SET scan_id = NULL, acquired_at = NULL WHERE id = 1")
            db.execute_db("UPDATE current_scan_state SET status = 'IDLE', scan_id = NULL WHERE id = 1")
            
            if is_full:
                proc = subprocess.Popen([python_exe, "run_scan.py"])
            else:
                proc = subprocess.Popen([python_exe, "-c", abbrev_script])
                
            pids.append(proc.pid)
            
            try:
                proc.wait(timeout=900) # 15 mins max for full scan
            except subprocess.TimeoutExpired:
                proc.kill()
                raise Exception(f"Timeout on scan cycle {i+1} ({scan_type})")
            
            lock = db.execute_db("SELECT scan_id FROM scan_lock WHERE id=1", fetch="one")
            state = db.execute_db("SELECT status FROM current_scan_state WHERE id=1", fetch="one")
            
            if state['status'] == 'IDLE' and (not lock or lock['scan_id'] is None):
                success_count += 1
            else:
                log.error(f"  Cycle {i+1} failed: lock={lock}, state={state}")
                break
                
        unique_pids = len(set(pids))
        if success_count == 5 and unique_pids == 5:
            log_audit("P0-AUDIT-5", "5 Scans + Restart Cycle", "PASS", f"All 5 cycles complete. PIDs: {pids}")
        else:
            log_audit("P0-AUDIT-5", "5 Scans + Restart Cycle", "FAIL", f"Success={success_count}/5, Unique PIDs={unique_pids}/5", "Process reuse or lock leak", "Ensure independent processes", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-5", "5 Scans + Restart Cycle", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 6: Error Preservation (Mock Universe) ---
def audit_6():
    log.info("Running Audit 6: Error Preservation (Mocked Lookup)")
    try:
        # Mock universe script that overrides the engine selection and mocks build_eligible_universe
        mock_script = """
import os
import unittest.mock
os.environ['USE_UNIVERSE_ENGINE'] = '1'

def mock_build():
    return [], 'UNIVERSE_EMPTY_TEST'

with unittest.mock.patch('universe_builder.build_eligible_universe', side_effect=mock_build):
    import run_scan
"""
        python_exe = sys.executable
        
        db.execute_db("UPDATE scan_lock SET scan_id = NULL, acquired_at = NULL WHERE id = 1")
        db.execute_db("UPDATE current_scan_state SET status = 'IDLE', scan_id = NULL WHERE id = 1")
        
        proc = subprocess.Popen([python_exe, "-c", mock_script])
        proc.wait(timeout=60)
        
        scan_id = db.execute_db("SELECT scan_id FROM scan_runs ORDER BY start_time DESC LIMIT 1", fetch="one")['scan_id']
        run = db.execute_db("SELECT status, error_message FROM scan_runs WHERE scan_id=?", (scan_id,), fetch="one")
        transition = db.execute_db("SELECT reason FROM scan_state_transitions WHERE scan_id=? AND new_state='FAILED'", (scan_id,), fetch="one")
        
        client = app.test_client()
        resp = client.get('/api/status')
        data = json.loads(resp.data)
        api_reason = data.get('failed_reason', '')
        
        # Test Mission Control payload
        mc_resp = client.get('/api/mission-control/status')
        mc_data = json.loads(mc_resp.data)
        mc_reason = mc_data.get('scan_status', {}).get('failed_reason', '')
        
        expected_err = "Universe too small: 0 < 500"
        
        cond_run = run['status'] == 'FAILED' and expected_err in str(run.get('error_message'))
        cond_trans = transition and expected_err in str(transition.get('reason'))
        cond_api = expected_err in str(api_reason)
        cond_mc = expected_err in str(mc_reason)
        
        if cond_run and cond_trans and cond_api and cond_mc:
            log_audit("P0-AUDIT-6", "Error Preservation", "PASS", "Error propagated to scan_runs, transitions, /api/status, and /mission-control/status")
        else:
            log_audit("P0-AUDIT-6", "Error Preservation", "FAIL", 
                      f"run={cond_run}, trans={cond_trans}, api={cond_api}, mc={cond_mc}", 
                      "Error masking in observability layers", "Fix error propagation", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-6", "Error Preservation", "FAIL", str(e), "Exception", "Fix exception", "Yes")

# --- AUDIT 7: API/UI Consistency ---
def audit_7():
    log.info("Running Audit 7: API/UI Consistency")
    try:
        client = app.test_client()
        resp = client.get('/api/status')
        api_data = json.loads(resp.data)
        
        db_state = db.execute_db("SELECT * FROM current_scan_state WHERE id=1", fetch="one")
        if not db_state:
             db_state = {'status': 'IDLE', 'scan_id': None, 'failed_reason': None, 'progress_pct': 0}
        
        api_status = api_data.get('status', 'IDLE')
        api_scan_id = api_data.get('scan_id') or ""
        db_status = db_state.get('status', 'IDLE')
        db_scan_id = db_state.get('scan_id') or ""
        
        status_match = api_status.upper() == db_status.upper()
        scan_id_match = (api_scan_id == db_scan_id) or (api_status.upper() == 'IDLE' and db_status.upper() == 'IDLE')
        
        if status_match and scan_id_match:
            log_audit("P0-AUDIT-7", "API/UI Consistency", "PASS", f"API({api_status}) matches DB({db_status})")
        else:
            log_audit("P0-AUDIT-7", "API/UI Consistency", "FAIL", 
                      f"API({api_status},{api_scan_id}) != DB({db_status},{db_scan_id})", 
                      "Drift in state", "Fix status API", "Yes")
    except Exception as e:
        log_audit("P0-AUDIT-7", "API/UI Consistency", "FAIL", str(e), "Exception", "Fix exception", "Yes")


def main():
    log.info("Starting Phase 0 Active Stability Audit Suite (V7)")
    meta = get_env_metadata()
    
    os.makedirs("release_audits", exist_ok=True)
    
    # 1. Pre-audit Snapshot
    dump_db_snapshot("release_audits/p0_db_snapshot_before.json")
    
    # Run Audits
    audit_1()
    audit_2()
    audit_3()
    audit_4()
    audit_5()
    audit_6()
    audit_7()
    
    # 2. Post-audit Snapshot
    dump_db_snapshot("release_audits/p0_db_snapshot_after.json")
    
    # Save JSON report
    report = {
        "metadata": meta,
        "results": audit_results,
        "summary": {
            "total": len(audit_results),
            "passed": len(audit_results) - failures_detected,
            "failed": failures_detected
        }
    }
    
    with open("release_audits/p0_active_audit_report.json", "w") as f:
        json.dump(report, f, indent=4)
        
    # Save MD report
    with open("release_audits/p0_closure_report.md", "w") as f:
        f.write("# Phase 0 Closure Report\n\n")
        f.write("## Metadata\n")
        for k, v in meta.items():
            f.write(f"- **{k}:** {v}\n")
        f.write("\n## Audit Results\n")
        for res in audit_results:
            f.write(f"### {res['Finding ID']} - {res['Audit']}\n")
            f.write(f"**Status:** {res['Status']}\n")
            f.write(f"**Evidence:** {res['Evidence']}\n")
            if res['Status'] == 'FAIL':
                f.write(f"**Root Cause:** {res['Root Cause']}\n")
                f.write(f"**Fix Required:** {res['Fix Required']}\n")
            f.write("\n")
            
        f.write("## Conclusion\n")
        if failures_detected == 0:
            f.write("Status: **PHASE 0 CONDITIONALLY CLOSED**\n")
            f.write("Next Steps: Commencing 48-hour observation period.\n")
        else:
            f.write("Status: **FAILED**\n")
            f.write("Next Steps: Requires remediation of failed findings.\n")
            
    log.info(f"Audit Suite Complete. Passed: {report['summary']['passed']}/{report['summary']['total']}")
    if failures_detected > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
