import re

log_files = {
    "scan_manual_1781856444_055982": "C:/Users/91971/.gemini/antigravity-ide/brain/4d16cedf-c7cf-4215-a140-2e9f88f24b5a/.system_generated/tasks/task-752.log",
    "scan_manual_1781858049_314672": "C:/Users/91971/.gemini/antigravity-ide/brain/4d16cedf-c7cf-4215-a140-2e9f88f24b5a/.system_generated/tasks/task-865.log"
}

patterns = {
    "start_time": r"Acquired: scan_id=.*",
    "phase1_start": r"Phase 1:.*done",
    "phase1_done": r"Phase 1 done",
    "phase2_start": r"Phase 2: jugaad_data fallback",
    "batch1": r"Phase 2 batch 1",
    "batch2": r"Phase 2 batch 2",
    "batch3": r"Phase 2 batch 3",
    "batch4": r"Phase 2 batch 4",
    "finalize_started": r"FINALIZE_STARTED",
    "save_results_completed": r"SAVE_RESULTS_COMPLETED",
    "snapshot_completed": r"SNAPSHOT_COMPLETED",
    "scan_completed": r"Done in",
    "watchdog_intervention": r"Zombie scan detected|WATCHDOG"
}

for scan, file in log_files.items():
    print(f"--- {scan} ---")
    try:
        with open(file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        found = {}
        for line in lines:
            if scan not in line and "Phase" not in line and "batch" not in line and "Done in" not in line and "FINALIZE" not in line and "SAVE" not in line and "SNAPSHOT" not in line and "Acquired" not in line and "ZOMBIE" not in line:
                continue
            
            for key, pattern in patterns.items():
                if re.search(pattern, line):
                    match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if match:
                        ts = match.group(1)
                        if key not in found:
                            found[key] = (ts, line.strip())
                        elif key.startswith('batch') or key == 'phase1_start':
                            found[key] = (ts, line.strip()) # keep last
                            
        for key in patterns.keys():
            if key in found:
                print(f"{key}: {found[key][0]} | {found[key][1]}")
            else:
                print(f"{key}: MISSING")
    except Exception as e:
        print(f"Error reading {file}: {e}")
    print()
