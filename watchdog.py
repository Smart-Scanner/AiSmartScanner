"""
Active Watchdog — Sections 3, 8, 19, 38 of the Master Plan.

Dedicated background thread that actively hunts for and recovers
stuck/stale scans. Replaces the passive _recover_stale() pattern.

Key behaviors:
  - Checks every WATCHDOG_CHECK_INTERVAL_SEC seconds
  - Queries scan_runs for status='running' older than SCAN_TIMEOUT_MIN
  - Transitions stale scans: running → stale → failed
  - Emits a heartbeat metric every loop for dead-man's-switch alerting
  - Respects shutdown_event for clean process termination
"""

import time
import logging
import threading
from datetime import datetime

log = logging.getLogger("watchdog")

# ── Configuration ─────────────────────────────────────────────────────
WATCHDOG_CHECK_INTERVAL_SEC = 60   # How often the watchdog loop runs
SCAN_TIMEOUT_MIN = 30              # Scans running longer than this are stale
HEARTBEAT_KEY = "watchdog_heartbeat_ts"

# ── Module state ──────────────────────────────────────────────────────
_watchdog_thread: threading.Thread | None = None
_shutdown_event: threading.Event | None = None


def start_watchdog(shutdown_event: threading.Event) -> threading.Thread:
    """Start the active watchdog background thread.

    Args:
        shutdown_event: Shared threading.Event for graceful shutdown.
    Returns:
        The watchdog Thread object (for join/status checks).
    """
    global _watchdog_thread, _shutdown_event
    _shutdown_event = shutdown_event

    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        log.info("[WATCHDOG] Already running")
        return _watchdog_thread

    _watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        name="watchdog",
        daemon=False,  # Managed lifecycle — NOT a fire-and-forget daemon
    )
    _watchdog_thread.start()
    log.info("[WATCHDOG] Started (interval=%ds, timeout=%dmin)",
             WATCHDOG_CHECK_INTERVAL_SEC, SCAN_TIMEOUT_MIN)
    return _watchdog_thread


def stop_watchdog():
    """Signal the watchdog to stop and wait for it to exit."""
    global _watchdog_thread
    if _shutdown_event:
        _shutdown_event.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=10)
        log.info("[WATCHDOG] Stopped")
    _watchdog_thread = None


def is_watchdog_healthy() -> bool:
    """Check if the watchdog has emitted a heartbeat recently.
    Used by /api/debug/health for dead-man's-switch alerting.
    """
    import db
    try:
        ts_str = db.get_meta(HEARTBEAT_KEY)
        if not ts_str:
            return False
        last_beat = float(ts_str)
        age_sec = time.time() - last_beat
        # Healthy if heartbeat is less than 5 minutes old
        return age_sec < (5 * 60)
    except Exception:
        return False


def _watchdog_loop():
    """Main watchdog loop. Runs until shutdown_event is set."""
    import db
    from events import (
        WATCHDOG_TRIGGERED, WATCHDOG_HEARTBEAT,
        SCAN_STALE, SCAN_RECOVERED, ACTOR_WATCHDOG,
    )

    log.info("[WATCHDOG] Loop started")

    # Brief startup delay to let DB init complete
    if _shutdown_event and _shutdown_event.wait(timeout=10):
        return  # Shutdown requested during startup

    while not (_shutdown_event and _shutdown_event.is_set()):
        try:
            _recover_stale_scans(db)
            _emit_heartbeat(db)
        except Exception as exc:
            log.error("[WATCHDOG] Loop error (continuing): %s", exc)

        # Wait for interval OR shutdown signal
        if _shutdown_event and _shutdown_event.wait(timeout=WATCHDOG_CHECK_INTERVAL_SEC):
            break  # Shutdown requested

    log.info("[WATCHDOG] Loop exiting")


def _recover_stale_scans(db):
    """Find and recover scans stuck in 'running' past the timeout."""
    from events import ACTOR_WATCHDOG

    try:
        # Query for stale running scans
        stale_scans = db.execute_db("""
            SELECT scan_id, start_time
            FROM scan_runs
            WHERE status = 'running'
        """, fetch="all")

        if not stale_scans:
            return

        now = datetime.now()
        for row in stale_scans:
            scan_id = row.get("scan_id", "")
            updated_at = row.get("start_time")
            if not updated_at:
                continue

            try:
                last_update = datetime.fromisoformat(str(updated_at))
                age_min = (now - last_update).total_seconds() / 60
            except (ValueError, TypeError):
                continue

            if age_min > SCAN_TIMEOUT_MIN:
                log.warning(
                    "[WATCHDOG] Stale scan detected: scan_id=%s, age=%.1f min. Recovering...",
                    scan_id, age_min
                )

                # Transition: running → failed (via watchdog recovery)
                # Uses conditional UPDATE to prevent race with a worker that might
                # still be alive and completing normally.
                recovered = db.transition_scan_state(
                    scan_id=scan_id,
                    from_status="running",
                    to_status="failed",
                    reason="watchdog_timeout",
                    actor=ACTOR_WATCHDOG,
                )

                if recovered:
                    log.warning(
                        "[WATCHDOG] Recovered stale scan: %s (was running for %.1f min)",
                        scan_id, age_min
                    )
                else:
                    log.info(
                        "[WATCHDOG] Scan %s already transitioned (race OK)", scan_id
                    )

    except Exception as exc:
        log.error("[WATCHDOG] Stale scan recovery failed: %s", exc)


def _emit_heartbeat(db):
    """Write heartbeat timestamp to scan_meta for health monitoring."""
    try:
        db.set_meta(HEARTBEAT_KEY, str(time.time()))
    except Exception as exc:
        log.warning("[WATCHDOG] Heartbeat write failed: %s", exc)
