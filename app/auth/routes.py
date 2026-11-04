import hashlib
import hmac
import os
import time

import bcrypt
from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required, login_user, logout_user

from app.decorators import log_audit_event
from app.extensions import db, limiter
from app.models import User, UserRole

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# --- OTP codes without server-side storage ---------------------------------
# The old version kept codes in an in-memory dict. On Vercel every request can
# land on a different function instance, so a code created by one instance was
# unknown to the next one and sign-up always failed with "invalid or expired".
#
# Now the code is derived from the phone number, the current time window and
# SECRET_KEY (an HMAC), so any instance can recompute and check it. A code is
# valid for the window it was issued in plus the next one (5 to 10 minutes).
OTP_TTL_SECONDS = 300


def _otp_window(offset=0):
    return int(time.time() // OTP_TTL_SECONDS) - offset


def _otp_for(phone: str, window: int) -> str:
    key = current_app.config["SECRET_KEY"].encode("utf-8")
    digest = hmac.new(key, f"{phone}:{window}".encode("utf-8"), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def _otp_is_valid(phone: str, code: str) -> bool:
    if not phone or not code:
        return False
    return any(
        hmac.compare_digest(_otp_for(phone, _otp_window(offset)), code)
        for offset in (0, 1)
    )


def _show_debug_otp() -> bool:
    # Set SHOW_DEBUG_OTP=false in Vercel once a real SMS provider is wired up.
    return os.environ.get("SHOW_DEBUG_OTP", "true").strip().lower() == "true"


def _hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(raw_password: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))


@auth_bp.route("/otp/request", methods=["POST"])
@limiter.limit("5 per minute")  # blunts OTP-spam / brute force
def request_otp():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    code = _otp_for(phone, _otp_window())

    # TODO: send `code` via your SMS provider instead of returning it.
    # Returned here only so the flow is testable before an SMS provider is wired up.
    response = {"message": "OTP sent"}
    if _show_debug_otp():
        response["debug_otp"] = code
    return jsonify(response), 200


@auth_bp.route("/otp/verify", methods=["POST"])
@limiter.limit("10 per minute")
def verify_otp_and_login():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()

    if not _otp_is_valid(phone, code):
        log_audit_event(action="otp_verify_failed", details={"phone": phone})
        return jsonify({"error": "invalid or expired code"}), 401

    user = User.query.filter_by(phone=phone).first()
    if user is None:
        if not name:
            return jsonify({"error": "name is required for new accounts"}), 400
        user = User(name=name, phone=phone, role=UserRole.CLIENT)
        db.session.add(user)
        db.session.commit()
        log_audit_event(action="user_registered", entity_type="user", entity_id=user.id, details={"via": "phone_otp"})

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "phone_otp"})
    return jsonify({"message": "logged in", "user_id": user.id, "role": user.role.value}), 200


@auth_bp.route("/google", methods=["POST"])
@limiter.limit("10 per minute")
def google_login():
    """
    Expects a verified Google ID token from the frontend (obtained via
    Google Sign-In). Verify it server-side before trusting any claims -
    never trust a "google_sub"/email sent raw from the client without
    verifying the token signature against Google's public keys.

    TODO: plug in google-auth / authlib token verification here using
    GOOGLE_CLIENT_ID from config. This stub assumes verification already
    happened and a verified payload was produced, to keep the ownership/
    account-linking logic clear.
    """
    data = request.get_json(silent=True) or {}
    id_token = data.get("id_token")
    if not id_token:
        return jsonify({"error": "id_token is required"}), 400

    # TODO: replace with real verification, e.g.:
    #   from google.oauth2 import id_token as google_id_token
    #   from google.auth.transport import requests as google_requests
    #   payload = google_id_token.verify_oauth2_token(
    #       id_token, google_requests.Request(), current_app.config["GOOGLE_CLIENT_ID"]
    #   )
    #   google_sub, email, name = payload["sub"], payload["email"], payload.get("name")
    return jsonify({
        "error": "Google verification not yet configured - see TODO in app/auth/routes.py"
    }), 501


@auth_bp.route("/complete-profile", methods=["POST"])
@login_required
def complete_profile():
    """
    A client who signed up via Google must add a phone number before they
    can check out, since delivery calls and mobile-money payments both
    require one.
    """
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    existing = User.query.filter_by(phone=phone).first()
    if existing and existing.id != current_user.id:
        return jsonify({"error": "phone already in use"}), 409

    current_user.phone = phone
    db.session.commit()
    return jsonify({"message": "profile updated"}), 200


@auth_bp.route("/staff-login", methods=["POST"])
@limiter.limit("10 per minute")
def staff_login():
    """
    Staff/owner accounts use email+password only (no Google login) to keep
    the admin-capable login surface smaller. Staff accounts are created by
    an owner via the admin blueprint, not through self-registration.
    The very first owner is created with create_owner.py.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in (UserRole.STAFF, UserRole.OWNER) or not _check_password(password, user.password_hash):
        log_audit_event(action="staff_login_failed", details={"email": email})
        return jsonify({"error": "invalid credentials"}), 401

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "staff_password"})
    return jsonify({"message": "logged in", "user_id": user.id, "role": user.role.value}), 200


# ---------------------------------------------------------------------------
# TEMPORARY: first-owner setup. DELETE this whole function (and the SETUP_KEY
# environment variable in Vercel) as soon as your owner account exists.
# It only works with the secret X-Setup-Key header, only while no owner
# exists, and it creates any missing database tables first.
# ---------------------------------------------------------------------------
@auth_bp.route("/bootstrap-owner", methods=["POST"])
@limiter.limit("5 per minute")
def bootstrap_owner():
    setup_key = os.environ.get("SETUP_KEY", "")
    supplied = request.headers.get("X-Setup-Key", "")
    if not setup_key or not hmac.compare_digest(setup_key.encode("utf-8"), supplied.encode("utf-8")):
        return jsonify({"error": "not found"}), 404

    db.create_all()

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip() or None
    password = data.get("password") or ""
    if not email or len(password) < 10:
        return jsonify({"error": "email and a password of at least 10 characters are required"}), 400

    existing = User.query.filter_by(email=email).first()
    if existing:
        if existing.role != UserRole.OWNER:
            return jsonify({"error": "that email belongs to a non-owner account"}), 409
        # Owner already exists with this email: reset the password.
        existing.password_hash = _hash_password(password)
        db.session.commit()
        return jsonify({"message": "owner password reset", "email": email}), 200

    if User.query.filter_by(role=UserRole.OWNER).first():
        return jsonify({"error": "an owner already exists with a different email"}), 409
    if not name:
        return jsonify({"error": "name is required to create the owner"}), 400

    user = User(name=name, email=email, phone=phone, role=UserRole.OWNER)
    user.password_hash = _hash_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "owner created", "email": email}), 201


# --- Google sign-in for AUTHORISED STAFF ONLY --------------------------------
# No account is ever created here. A Google account can only log in if an owner
# has already created a STAFF/OWNER user with that exact (verified) email.
@auth_bp.route("/google-config", methods=["GET"])
def google_config():
    # The client ID is public by design; the page needs it to show the button.
    return jsonify({"client_id": current_app.config.get("GOOGLE_CLIENT_ID") or ""}), 200


@auth_bp.route("/staff-google", methods=["POST"])
@limiter.limit("10 per minute")
def staff_google_login():
    data = request.get_json(silent=True) or {}
    credential = data.get("credential") or ""
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not credential or not client_id:
        return jsonify({"error": "Google sign-in is not configured"}), 400

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        # Verifies signature, expiry and that the token was issued for OUR client ID.
        payload = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id
        )
    except Exception:
        log_audit_event(action="staff_google_failed", details={"reason": "token_rejected"})
        return jsonify({"error": "invalid credentials"}), 401

    email = (payload.get("email") or "").strip().lower()
    if not email or not payload.get("email_verified"):
        log_audit_event(action="staff_google_failed", details={"reason": "email_unverified"})
        return jsonify({"error": "invalid credentials"}), 401

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in (UserRole.STAFF, UserRole.OWNER):
        log_audit_event(action="staff_google_failed", details={"email": email, "reason": "not_authorised"})
        return jsonify({"error": "this Google account is not authorised for staff access"}), 403

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "staff_google"})
    return jsonify({"message": "logged in", "user_id": user.id, "role": user.role.value}), 200


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_audit_event(action="logout", entity_type="user", entity_id=current_user.id)
    logout_user()
    return jsonify({"message": "logged out"}), 200


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    # Returns ONLY the logged-in user's own data - never accepts a
    # user_id param here, precisely so this route can't be used to probe
    # other accounts.
    return jsonify({
        "id": current_user.id,
        "name": current_user.name,
        "phone": current_user.phone,
        "email": current_user.email,
        "role": current_user.role.value,
        "points_balance": current_user.points_balance,
    }), 200
