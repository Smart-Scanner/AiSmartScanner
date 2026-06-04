"""
Google OAuth login + auth decorators.

Session schema:
    session['user_id']   int   — auth_db.users.id
    session['email']     str   — for admin-allowlist checks
    session['next']      str   — post-login redirect target (cleared after use)
"""

import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional

import requests
from flask import Blueprint, redirect, url_for, session, request, render_template, abort, jsonify, flash
from authlib.integrations.flask_client import OAuth

import auth_db
from config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    FINGERPRINT_PUBLIC_KEY, FINGERPRINT_SECRET_KEY, FINGERPRINT_API_REGION,
)

# ---------------------------------------------------------------------------
# Hardcoded local dev credentials  (username: admin  /  password: admin123)
# ---------------------------------------------------------------------------
_LOCAL_USERNAME = "admin"
_LOCAL_PASSWORD = "admin123"
_LOCAL_EMAIL    = "admin@local.dev"


def _has_access(user) -> bool:
    """True if user has either an active trial OR a non-expired subscription.

    Admins are NOT checked here — caller should treat is_admin as always-true.
    """
    now = datetime.now(timezone.utc)

    sub_expires = user["sub_expires_at"]
    if sub_expires:
        try:
            if datetime.fromisoformat(sub_expires) > now:
                return True
        except ValueError:
            pass

    trial_started = user["trial_started_at"]
    if trial_started:
        try:
            trial_days = int(auth_db.get_setting("trial_duration_days", "3"))
            trial_end = datetime.fromisoformat(trial_started) + timedelta(days=trial_days)
            if trial_end > now:
                return True
        except (ValueError, TypeError):
            pass

    return False

log = logging.getLogger("auth")

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
oauth = OAuth()


def init_oauth(app) -> None:
    """Register the Google provider on the Flask app's OAuth client."""
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


# ─────────────────────────── routes ───────────────────────────

@auth_bp.route("/google/login")
def google_login():
    """Redirect to local login for dev. (Google OAuth disabled for local use.)"""
    return redirect(url_for("auth.local_login"))


@auth_bp.route("/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception as exc:
        log.warning("OAuth callback failed: %s", exc)
        return render_template("auth_error.html", message="Google sign-in failed. Please try again."), 400

    userinfo = token.get("userinfo") or {}
    sub = userinfo.get("sub")
    email = (userinfo.get("email") or "").lower()
    name = userinfo.get("name") or email
    picture = userinfo.get("picture") or ""
    email_verified = userinfo.get("email_verified", False)

    if not sub or not email:
        return render_template("auth_error.html", message="Google did not return an email. Please retry."), 400
    if not email_verified:
        return render_template("auth_error.html", message="Your Google email is not verified."), 400

    user = auth_db.upsert_oauth_user(google_sub=sub, email=email, name=name, picture_url=picture)
    auth_db.mark_login(user["id"])

    session.clear()
    session["user_id"] = user["id"]
    session["email"] = user["email"]

    if user["status"] == "approved":
        next_url = session.pop("post_login_next", None) or url_for("pages.index")
        return redirect(next_url)
    # suspended / rejected — admin-revoked accounts only (no 'pending' anymore)
    return render_template(
        "auth_error.html",
        message=f"Your account is {user['status']}. Contact the administrator.",
    ), 403


_FPJS_HOST_BY_REGION = {
    "us": "api.fpjs.io",
    "eu": "eu.api.fpjs.io",
    "ap": "ap.api.fpjs.io",
}


def _verify_with_fingerprint_pro(request_id: str) -> Optional[dict]:
    """Server-side verification via fingerprint.com Server API.

    Returns {visitor_id, confidence, ip} on success, None on failure or
    when the secret key isn't configured.
    """
    if not FINGERPRINT_SECRET_KEY or not request_id:
        return None
    host = _FPJS_HOST_BY_REGION.get(FINGERPRINT_API_REGION, "api.fpjs.io")
    try:
        r = requests.get(
            f"https://{host}/events/{request_id}",
            headers={"Auth-API-Key": FINGERPRINT_SECRET_KEY},
            timeout=5,
        )
    except requests.RequestException as exc:
        log.warning("FPJS verify network error: %s", exc)
        return None
    if r.status_code != 200:
        log.warning("FPJS verify HTTP %s: %s", r.status_code, r.text[:200])
        return None
    ident = (((r.json() or {}).get("products") or {}).get("identification") or {}).get("data") or {}
    visitor_id = ident.get("visitorId")
    if not visitor_id:
        log.warning("FPJS verify: no visitorId in API response")
        return None
    return {
        "visitor_id": visitor_id,
        "confidence": (ident.get("confidence") or {}).get("score"),
        "ip": ident.get("ip"),
    }


@auth_bp.route("/device/verify", methods=["GET"])
def device_verify():
    """Page that loads FingerprintJS, captures requestId, and POSTs to /device/claim."""
    if not session.get("user_id"):
        return redirect(url_for("auth.local_login"))
    user = auth_db.get_user_by_id(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("auth.local_login"))
    if user["is_admin"]:
        session["device_verified"] = True
        return redirect(url_for("pages.index"))
    return render_template(
        "device_verify.html",
        user=user,
        fp_public_key=FINGERPRINT_PUBLIC_KEY,
        fp_region=FINGERPRINT_API_REGION,
    )


@auth_bp.route("/device/claim", methods=["POST"])
def device_claim():
    """JSON endpoint called by the verify page.

    Pro mode (FINGERPRINT_SECRET_KEY set): client sends requestId, server
    verifies via fingerprint.com Server API and extracts the trusted visitorId.
    OSS fallback: client sends visitor_id directly (trusted blindly).
    """
    if not session.get("user_id"):
        return jsonify({"error": "not_signed_in"}), 401
    user = auth_db.get_user_by_id(session["user_id"])
    if not user:
        session.clear()
        return jsonify({"error": "user_not_found"}), 401
    if user["is_admin"]:
        session["device_verified"] = True
        return jsonify({"state": "active", "admin_exempt": True})

    data = request.get_json(silent=True) or {}

    if FINGERPRINT_SECRET_KEY:
        request_id = (data.get("request_id") or "").strip()
        if not request_id:
            return jsonify({"error": "missing_request_id"}), 400
        verified = _verify_with_fingerprint_pro(request_id)
        if not verified:
            return jsonify({"error": "verification_failed"}), 400
        visitor_id = verified["visitor_id"]
        confidence = verified.get("confidence")
    else:
        visitor_id = (data.get("visitor_id") or "").strip()
        confidence = data.get("confidence")

    if not visitor_id or len(visitor_id) < 8:
        return jsonify({"error": "bad_visitor_id"}), 400

    ua = (request.headers.get("User-Agent") or "")[:500]
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""

    result = auth_db.claim_device(user["id"], visitor_id, confidence, ua, ip)
    if result["state"] == auth_db.CLAIM_OK_ACTIVE:
        session["device_verified"] = True
    return jsonify({"state": result["state"]})


@auth_bp.route("/local-login", methods=["GET", "POST"])
def local_login():
    """Simple hardcoded username/password login for local development.

    Credentials: admin / admin123
    Creates (or reuses) a local admin user so the full app is accessible
    without setting up Google OAuth.
    """
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == _LOCAL_USERNAME and password == _LOCAL_PASSWORD:
            # Get or create the hardcoded local admin user
            user = auth_db.get_or_create_local_admin(_LOCAL_EMAIL, name="Local Admin")
            session.clear()
            session["user_id"] = user["id"]
            session["email"]   = user["email"]
            session["device_verified"] = True   # skip device-fingerprint gate
            next_url = request.args.get("next") or url_for("pages.index")
            return redirect(next_url)
        error = "Invalid username or password."

    return render_template("local_login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("pages.index"))


# ─────────────────────────── decorators ───────────────────────────

def _current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return auth_db.get_user_by_id(uid)


def login_required(f):
    """Any signed-in user (pending or approved). Used for the /pending page."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.local_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def approved_required(f):
    """Signed-in, status='approved', AND (admin OR device verified)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.local_login", next=request.path))
        user = _current_user()
        if not user:
            session.clear()
            return redirect(url_for("auth.local_login"))
        if user["status"] != "approved":
            session.clear()
            return redirect(url_for("pages.index"))
        # Device-binding: admins exempt; everyone else needs device_verified set
        # by /auth/device/claim before proceeding.
        if not user["is_admin"] and not session.get("device_verified"):
            return redirect(url_for("auth.device_verify"))
        return f(*args, **kwargs)
    return wrapper


def subscribed_required(f):
    """Approved + device verified + (admin OR has trial/sub access).

    Strict superset of approved_required: same gates, plus a final
    has-access check that lets users through only while their trial or
    paid subscription is still valid. Expired users land on /subscribe.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.local_login", next=request.path))
        user = _current_user()
        if not user:
            session.clear()
            return redirect(url_for("auth.local_login"))
        if user["status"] != "approved":
            session.clear()
            return redirect(url_for("pages.index"))
        if not user["is_admin"] and not session.get("device_verified"):
            return redirect(url_for("auth.device_verify"))
        if not user["is_admin"] and not _has_access(user):
            return redirect(url_for("pages.subscribe"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Signed-in AND status='approved' AND users.is_admin=1."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.local_login", next=request.path))
        user = _current_user()
        if not user or user["status"] != "approved" or not user["is_admin"]:
            abort(403)
        return f(*args, **kwargs)
    return wrapper
