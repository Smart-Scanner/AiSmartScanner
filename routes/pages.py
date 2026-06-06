"""HTML page routes."""

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, render_template, session, redirect, url_for, send_from_directory, abort, request
from werkzeug.utils import secure_filename

log = logging.getLogger("pages")

import auth_db
from routes.auth import approved_required, subscribed_required, _has_access

pages_bp = Blueprint("pages", __name__)


def _trial_status(user, trial_days: int) -> dict:
    """For the dashboard banner: 'in_trial' (days_left), 'subscribed' (days_left), or None."""
    if user["is_admin"]:
        return None
    now = datetime.now(timezone.utc)
    if user["sub_expires_at"]:
        try:
            exp = datetime.fromisoformat(user["sub_expires_at"])
            if exp > now:
                return {"kind": "subscribed", "days_left": (exp - now).days}
        except ValueError:
            pass
    if user["trial_started_at"]:
        try:
            end = datetime.fromisoformat(user["trial_started_at"]) + timedelta(days=trial_days)
            if end > now:
                return {"kind": "trial", "days_left": (end - now).days, "hours_left": int((end - now).total_seconds() // 3600)}
        except ValueError:
            pass
    return None


@pages_bp.route("/")
def index():
    """Smart route:
       - logged out → public landing page
       - logged in + needs device verify → /auth/device/verify
       - logged in + trial/sub expired → /subscribe
       - logged in + has access → dashboard
    """
    if not session.get("user_id"):
        plans = auth_db.list_plans(active_only=True)
        return render_template("landing.html", plans=plans, active="home")

    user = auth_db.get_user_by_id(session["user_id"])
    if not user:
        session.clear()
        plans = auth_db.list_plans(active_only=True)
        return render_template("landing.html", plans=plans, active="home")
    if user["status"] != "approved":
        session.clear()
        plans = auth_db.list_plans(active_only=True)
        return render_template("landing.html", plans=plans, active="home")

    if not user["is_admin"] and not session.get("device_verified"):
        return redirect(url_for("auth.device_verify"))

    if not user["is_admin"] and not _has_access(user):
        return redirect(url_for("pages.subscribe"))

    trial_days = int(auth_db.get_setting("trial_duration_days", "3"))
    return render_template(
        "v3/dashboard.html",
        user=user,
        is_admin=bool(user["is_admin"]),
        trial_status=_trial_status(user, trial_days),
    )


# ── V2 Backward Compatibility ────────────────────────────────────────
@pages_bp.route("/v2")
@subscribed_required
def v2_dashboard():
    """Legacy V2 dashboard — keep for 2-4 weeks after V3 launch."""
    user = auth_db.get_user_by_id(session["user_id"])
    trial_days = int(auth_db.get_setting("trial_duration_days", "3"))
    return render_template(
        "index.html",
        user=user,
        is_admin=bool(user["is_admin"]),
        trial_status=_trial_status(user, trial_days),
    )


# ── V3 Page Routes ───────────────────────────────────────────────────
@pages_bp.route("/top-picks")
@subscribed_required
def top_picks():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/top_picks.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/golden")
@subscribed_required
def golden_stocks():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/golden.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/hc")
@subscribed_required
def high_conviction():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/high_conviction.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/breakouts")
@subscribed_required
def breakouts_page():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/breakouts.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/market")
@subscribed_required
def market_intel():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/market_intel.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/paper-trades-view")
@subscribed_required
def paper_trades_view():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/paper_trades.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/outcome")
@subscribed_required
def outcome_intelligence():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/outcome.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/watchlist")
@subscribed_required
def watchlist_page():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/watchlist.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/settings")
@subscribed_required
def settings_page():
    user = auth_db.get_user_by_id(session["user_id"])
    return render_template("v3/settings.html", user=user, is_admin=bool(user["is_admin"]))


@pages_bp.route("/pricing")
def pricing():
    plans = auth_db.list_plans(active_only=True)
    return render_template("pricing.html", plans=plans, active="pricing")


@pages_bp.route("/about")
def about():
    return render_template("about.html", active="about")


@pages_bp.route("/contact")
def contact():
    return render_template("contact.html", active="contact")


UPLOAD_BASE = Path(__file__).resolve().parent.parent / "cache" / "uploads"
PAYMENT_UPLOAD_DIR = UPLOAD_BASE / "payments"
PAYMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PAYMENT_ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
PAYMENT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB (screenshots can be larger than QR)


@pages_bp.route("/uploads/qr/<path:filename>")
def serve_qr(filename):
    """Serve admin-uploaded QR images. No auth — QR contains a public UPI handle."""
    qr_dir = UPLOAD_BASE / "qr"
    if not (qr_dir / filename).is_file():
        abort(404)
    return send_from_directory(qr_dir, filename)


@pages_bp.route("/uploads/payments/<path:filename>")
@approved_required
def serve_payment_screenshot(filename):
    """Serve a payment screenshot. Restricted: only admins, or the user who uploaded it."""
    if not (PAYMENT_UPLOAD_DIR / filename).is_file():
        abort(404)
    user = auth_db.get_user_by_id(session["user_id"])
    if not user:
        abort(403)
    # Admins can view all
    if user["is_admin"]:
        return send_from_directory(PAYMENT_UPLOAD_DIR, filename)
    # Non-admins: only the file referenced by their own submission(s)
    import sqlite3
    conn = auth_db._get_conn()
    row = conn.execute(
        "SELECT 1 FROM payment_submissions WHERE user_id=? AND screenshot_path=?",
        (user["id"], f"payments/{filename}"),
    ).fetchone()
    if not row:
        abort(403)
    return send_from_directory(PAYMENT_UPLOAD_DIR, filename)


def _save_payment_screenshot(file_storage, user_id: int) -> tuple[bool, str]:
    """Validate and persist a payment-proof screenshot.

    Returns (ok, value_or_error). On success, value is the path stored in DB
    (e.g. 'payments/<filename>').
    """
    if not file_storage or not file_storage.filename:
        return True, ""  # screenshot is optional
    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return False, "screenshot-bad-ext"
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in PAYMENT_ALLOWED_EXT:
        return False, "screenshot-bad-ext"
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > PAYMENT_MAX_BYTES:
        return False, "screenshot-too-large"
    if size == 0:
        return True, ""  # treat empty as no upload
    new_name = f"pay_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
    file_storage.save(str(PAYMENT_UPLOAD_DIR / new_name))
    return True, f"payments/{new_name}"


def _delete_payment_screenshot(rel_path: str) -> None:
    if not rel_path:
        return
    base = rel_path.split("/", 1)[-1]
    target = PAYMENT_UPLOAD_DIR / base
    try:
        if target.is_file():
            target.unlink()
    except OSError as exc:
        log.warning("Could not delete payment screenshot %s: %s", target, exc)


@pages_bp.route("/subscribe/submit-payment", methods=["POST"])
@approved_required
def submit_payment():
    """User submits UTR + screenshot after paying offline."""
    user = auth_db.get_user_by_id(session["user_id"])
    if not user or user["is_admin"]:
        # Admins don't subscribe; nothing to submit
        return redirect(url_for("pages.subscribe"))

    try:
        plan_id = int(request.form["plan_id"])
    except (KeyError, ValueError):
        return redirect(url_for("pages.subscribe", msg="bad-input"))
    utr = (request.form.get("utr") or "").strip()
    note = (request.form.get("note") or "").strip()[:500] or None

    if len(utr) < 4:
        return redirect(url_for("pages.subscribe", msg="utr-too-short"))
    plan = auth_db.get_plan(plan_id)
    if not plan or not plan["is_active"]:
        return redirect(url_for("pages.subscribe", msg="plan-not-found"))

    ok, val = _save_payment_screenshot(request.files.get("screenshot"), user["id"])
    if not ok:
        return redirect(url_for("pages.subscribe", msg=val))
    screenshot_path = val or None

    _, prior_screenshot = auth_db.submit_payment(
        user_id=user["id"],
        plan_id=plan_id,
        utr=utr,
        screenshot_path=screenshot_path,
        note=note,
    )
    if prior_screenshot:
        _delete_payment_screenshot(prior_screenshot)
    return redirect(url_for("pages.subscribe", msg="payment-submitted"))


@pages_bp.route("/stock/<symbol>")
@subscribed_required
def stock_detail(symbol):
    return render_template("stock_detail.html", symbol=symbol.upper())


@pages_bp.route("/portfolio")
@pages_bp.route("/portfolio/<int:pid>")
@subscribed_required
def portfolio_page(pid=None):
    return render_template("portfolio.html", portfolio_id=pid)


@pages_bp.route("/subscribe")
@approved_required
def subscribe():
    """Shown to users whose trial expired and have no active subscription."""
    user = auth_db.get_user_by_id(session["user_id"])
    # If they actually still have access, bounce them back to the dashboard.
    if user["is_admin"] or _has_access(user):
        return redirect(url_for("pages.index"))
    plans = auth_db.list_plans(active_only=True)
    pending = auth_db.get_pending_payment_for_user(user["id"])
    return render_template(
        "subscribe.html",
        user=user,
        plans=plans,
        pending=pending,
        msg=request.args.get("msg", ""),
    )
