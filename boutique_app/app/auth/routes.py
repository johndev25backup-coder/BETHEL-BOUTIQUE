import random
from datetime import datetime, timedelta

import bcrypt
from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required, login_user, logout_user

from app.decorators import log_audit_event
from app.extensions import db, limiter
from app.models import User, UserRole

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# In-memory OTP store for demo purposes only.
# In production, back this with Redis (with an expiry) rather than a
# plain dict, since this dict is lost on restart and doesn't scale
# beyond a single process.
_OTP_STORE = {}
OTP_TTL_SECONDS = 300


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

    code = f"{random.randint(0, 999999):06d}"
    _OTP_STORE[phone] = {
        "code": code,
        "expires_at": datetime.utcnow() + timedelta(seconds=OTP_TTL_SECONDS),
    }

    # TODO: send `code` via your SMS provider instead of returning it.
    # Returned here only so the flow is testable before an SMS provider is wired up.
    return jsonify({"message": "OTP sent", "debug_otp": code}), 200


@auth_bp.route("/otp/verify", methods=["POST"])
@limiter.limit("10 per minute")
def verify_otp_and_login():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()

    record = _OTP_STORE.get(phone)
    if not record or record["code"] != code or datetime.utcnow() > record["expires_at"]:
        log_audit_event(action="otp_verify_failed", details={"phone": phone})
        return jsonify({"error": "invalid or expired code"}), 401

    del _OTP_STORE[phone]

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
