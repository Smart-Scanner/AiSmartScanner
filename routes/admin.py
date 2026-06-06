"""
Admin dashboard at /admin.

Single-page UI with top tabs (Users / Plans / Settings / Admins). All
write actions POST back to dedicated endpoints which redirect to /admin
preserving the active tab via ?tab=<name>.
"""

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, render_template, redirect, url_for, session, request
from werkzeug.utils import secure_filename

import auth_db
from routes.auth import admin_required


QR_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "cache" / "uploads" / "qr"
QR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
QR_ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
QR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

log = logging.getLogger("admin")
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _redir(tab: str = "users", flash: str = ""):
    url = url_for("admin.dashboard", tab=tab)
    if flash:
        url += f"&msg={flash}"
    return redirect(url)


def _save_qr_upload(file_storage, plan_id_for_name: str = "new") -> tuple[bool, str]:
    """Validate + persist an uploaded QR image. Returns (ok, value_or_error).

    ok=True → value is the relative path stored in DB (e.g. 'qr/<filename>')
    ok=False → value is a short error code suitable for the ?msg flash
    """
    if not file_storage or not file_storage.filename:
        return False, "no-file"
    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return False, "qr-bad-ext"
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in QR_ALLOWED_EXT:
        return False, "qr-bad-ext"
    # Size check (stream to disk after, but peek first)
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > QR_MAX_BYTES:
        return False, "qr-too-large"
    if size == 0:
        return False, "no-file"
    new_name = f"plan_{plan_id_for_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
    out_path = QR_UPLOAD_DIR / new_name
    file_storage.save(str(out_path))
    return True, f"qr/{new_name}"


def _delete_qr_file(rel_path: str) -> None:
    if not rel_path:
        return
    # rel_path looks like 'qr/plan_x_y.png' — strip the leading 'qr/' to land in QR_UPLOAD_DIR
    base = rel_path.split("/", 1)[-1]
    target = QR_UPLOAD_DIR / base
    try:
        if target.is_file():
            target.unlink()
    except OSError as exc:
        log.warning("Could not delete QR file %s: %s", target, exc)


def _compute_trial_state(user, trial_days: int) -> dict:
    """Returns a dict with keys: state ('admin'/'trial_active'/'trial_expired'/'subscribed'/'sub_expired'),
    label (human readable), days_left (int or None)."""
    if user["is_admin"]:
        return {"state": "admin", "label": "Admin (no limit)", "days_left": None}

    now = datetime.now(timezone.utc)

    # Subscription check first — active sub overrides trial state
    if user["sub_expires_at"]:
        try:
            sub_exp = datetime.fromisoformat(user["sub_expires_at"])
            if sub_exp > now:
                days = (sub_exp - now).days
                return {"state": "subscribed", "label": f"Subscribed ({days}d left)", "days_left": days}
            return {"state": "sub_expired", "label": "Subscription expired", "days_left": 0}
        except ValueError:
            pass

    # Trial state
    if user["trial_started_at"]:
        try:
            start = datetime.fromisoformat(user["trial_started_at"])
            end = start + timedelta(days=trial_days)
            if end > now:
                days = (end - now).days
                return {"state": "trial_active", "label": f"Trial ({days}d left)", "days_left": days}
            return {"state": "trial_expired", "label": "Trial expired", "days_left": 0}
        except ValueError:
            pass

    return {"state": "no_trial", "label": "Not started", "days_left": None}


@admin_bp.route("/", methods=["GET"])
@admin_required
def dashboard():
    tab = request.args.get("tab", "users")
    msg = request.args.get("msg", "")

    trial_days = int(auth_db.get_setting("trial_duration_days", "3"))
    users = auth_db.list_all_users_with_plan()
    plans = auth_db.list_plans()
    admins = auth_db.list_admins()
    pending_payments = auth_db.list_pending_payments()

    enriched_users = []
    for u in users:
        trial = _compute_trial_state(u, trial_days)
        enriched_users.append({**dict(u), "trial": trial})

    current = auth_db.get_user_by_id(session["user_id"])

    return render_template(
        "admin_dashboard.html",
        tab=tab,
        msg=msg,
        current_user=current,
        users=enriched_users,
        plans=plans,
        admins=admins,
        pending_payments=pending_payments,
        trial_days=trial_days,
    )


# ───────────────────────── plans ─────────────────────────

@admin_bp.route("/plans", methods=["POST"])
@admin_required
def plans_create():
    try:
        name = request.form["name"].strip()
        duration_days = int(request.form["duration_days"])
        price_inr = int(request.form["price_inr"])
    except (KeyError, ValueError):
        return _redir("plans", "bad-input")
    if not name or duration_days <= 0 or price_inr < 0:
        return _redir("plans", "bad-input")
    upi_id = (request.form.get("upi_id") or "").strip() or None

    qr_path = None
    qr_file = request.files.get("qr_image")
    if qr_file and qr_file.filename:
        ok, val = _save_qr_upload(qr_file, plan_id_for_name="new")
        if not ok:
            return _redir("plans", val)
        qr_path = val
    auth_db.create_plan(name, duration_days, price_inr, upi_id=upi_id, qr_image_path=qr_path)
    return _redir("plans", "plan-created")


@admin_bp.route("/plans/<int:plan_id>/edit", methods=["POST"])
@admin_required
def plans_edit(plan_id):
    try:
        name = request.form["name"].strip()
        duration_days = int(request.form["duration_days"])
        price_inr = int(request.form["price_inr"])
    except (KeyError, ValueError):
        return _redir("plans", "bad-input")
    if not name or duration_days <= 0 or price_inr < 0:
        return _redir("plans", "bad-input")
    upi_id = (request.form.get("upi_id") or "").strip() or None
    clear_qr = request.form.get("clear_qr") == "on"

    existing = auth_db.get_plan(plan_id)
    new_qr_path = None
    qr_file = request.files.get("qr_image")
    if qr_file and qr_file.filename:
        ok, val = _save_qr_upload(qr_file, plan_id_for_name=str(plan_id))
        if not ok:
            return _redir("plans", val)
        new_qr_path = val
        if existing and existing["qr_image_path"]:
            _delete_qr_file(existing["qr_image_path"])
    elif clear_qr and existing and existing["qr_image_path"]:
        _delete_qr_file(existing["qr_image_path"])

    auth_db.update_plan(plan_id, name, duration_days, price_inr,
                         upi_id=upi_id, qr_image_path=new_qr_path, clear_qr=clear_qr)
    return _redir("plans", "plan-updated")


@admin_bp.route("/plans/<int:plan_id>/toggle", methods=["POST"])
@admin_required
def plans_toggle(plan_id):
    new_state = auth_db.toggle_plan_active(plan_id)
    msg = "plan-activated" if new_state else "plan-deactivated"
    return _redir("plans", msg)


@admin_bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
@admin_required
def plans_delete(plan_id):
    ok, qr_path = auth_db.delete_plan(plan_id)
    if ok and qr_path:
        _delete_qr_file(qr_path)
    return _redir("plans", "plan-deleted" if ok else "plan-not-found")


# ───────────────────────── settings ─────────────────────────

@admin_bp.route("/settings/trial", methods=["POST"])
@admin_required
def settings_trial():
    try:
        days = int(request.form["trial_duration_days"])
    except (KeyError, ValueError):
        return _redir("settings", "bad-input")
    if days < 0 or days > 365:
        return _redir("settings", "bad-input")
    auth_db.set_setting("trial_duration_days", str(days))
    return _redir("settings", "settings-saved")


# ───────────────────────── admins ─────────────────────────

@admin_bp.route("/admins", methods=["POST"])
@admin_required
def admins_add():
    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email:
        return _redir("admins", "bad-input")
    auth_db.add_admin_by_email(email)
    return _redir("admins", "admin-added")


@admin_bp.route("/admins/<int:user_id>/demote", methods=["POST"])
@admin_required
def admins_demote(user_id):
    # Prevent self-demotion (lockout risk)
    if user_id == session.get("user_id"):
        return _redir("admins", "no-self-demote")
    auth_db.set_user_admin(user_id, False)
    return _redir("admins", "admin-demoted")

# ───────────────────────── users (subscription actions) ─────────────────────────

@admin_bp.route("/users/<int:user_id>/subscribe", methods=["POST"])
@admin_required
def users_subscribe(user_id):
    """Assign / extend a subscription on a user (manual 'Mark paid' action)."""
    try:
        plan_id = int(request.form["plan_id"])
    except (KeyError, ValueError):
        return _redir("users", "bad-input")
    updated = auth_db.assign_subscription(user_id, plan_id)
    msg = "subscription-assigned" if updated else "subscription-failed"
    return _redir("users", msg)


@admin_bp.route("/users/<int:user_id>/suspend", methods=["POST"])
@admin_required
def users_suspend(user_id):
    if user_id == session.get("user_id"):
        return _redir("users", "no-self-action")
    ok = auth_db.suspend_user(user_id, session.get("email", ""))
    return _redir("users", "user-suspended" if ok else "user-not-found")


@admin_bp.route("/users/<int:user_id>/unsuspend", methods=["POST"])
@admin_required
def users_unsuspend(user_id):
    ok = auth_db.unsuspend_user(user_id, session.get("email", ""))
    return _redir("users", "user-unsuspended" if ok else "user-not-found")


@admin_bp.route("/users/<int:user_id>/expire", methods=["POST"])
@admin_required
def users_expire(user_id):
    if user_id == session.get("user_id"):
        return _redir("users", "no-self-action")
    ok = auth_db.expire_user_access(user_id, session.get("email", ""))
    return _redir("users", "user-expired" if ok else "user-not-found")


# ───────────────────────── payment submissions ─────────────────────────

@admin_bp.route("/payments/<int:submission_id>/approve", methods=["POST"])
@admin_required
def payments_approve(submission_id):
    ok, _user = auth_db.approve_payment(submission_id, session.get("email", ""))
    return _redir("payments", "payment-approved" if ok else "payment-not-found")


@admin_bp.route("/payments/<int:submission_id>/reject", methods=["POST"])
@admin_required
def payments_reject(submission_id):
    note = (request.form.get("review_note") or "").strip()[:500] or None
    ok = auth_db.reject_payment(submission_id, session.get("email", ""), note)
    return _redir("payments", "payment-rejected" if ok else "payment-not-found")


# ───────────────────────── DLQ admin (Phase 5) ─────────────────────────

@admin_bp.route("/dlq-replay", methods=["GET"])
@admin_required
def dlq_replay():
    """Replay dead-letter queue entries."""
    from flask import jsonify
    import db
    replayed = db.replay_dlq()
    pending = db.dlq_entry_count()
    return jsonify({"replayed": replayed, "pending": pending})


# ───────────────────────── Scan control (Phase 6) ─────────────────────────

@admin_bp.route("/cancel-scan", methods=["POST", "GET"])
@admin_required
def cancel_scan():
    """Request cancellation of current scan."""
    from flask import jsonify
    import db
    if not db.scan_state.is_scanning:
        return jsonify({"status": "no_scan_running"})
    db.scan_state.cancel_requested = True
    log.info("Scan cancellation requested by admin: %s", session.get("email", "?"))
    return jsonify({"status": "cancel_requested"})


@admin_bp.route("/scan-history", methods=["GET"])
@admin_required
def scan_history():
    """Get recent scan runs."""
    from flask import jsonify
    import db
    limit = int(request.args.get("limit", "10"))
    runs = db.get_recent_scan_runs(limit=min(limit, 50))
    return jsonify({"runs": runs})


# ───────────────────────── Symbol freshness (Phase 7) ─────────────────────────

@admin_bp.route("/mark-deep-scan/<symbol>", methods=["GET", "POST"])
@admin_required
def mark_deep_scan(symbol):
    """Manually flag a symbol for deep scan."""
    from flask import jsonify
    import db
    db.mark_deep_scan_needed(symbol.upper(), reason="admin_manual")
    log.info("Deep scan flagged for %s by admin: %s", symbol.upper(), session.get("email", "?"))
    return jsonify({"status": "flagged", "symbol": symbol.upper()})
