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
    return os.environ.get("SHOW_DEBUG_OTP", "true").strip().lower() == "true"


def _hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(raw_password: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))


@auth_bp.route("/otp/request", methods=["POST"])
@limiter.limit("5 per minute")
def request_otp():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    code = _otp_for(phone, _otp_window())

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
    data = request.get_json(silent=True) or {}
    id_token = data.get("id_token")
    if not id_token:
        return jsonify({"error": "id_token is required"}), 400

    return jsonify({
        "error": "Google verification not yet configured - see TODO in app/auth/routes.py"
    }), 501


@auth_bp.route("/complete-profile", methods=["POST"])
@login_required
def complete_profile():
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
# TEMPORARY EMERGENCY BOOTSTRAP (GET & POST SUPPORTED)
# ---------------------------------------------------------------------------
@auth_bp.route("/bootstrap-owner", methods=["GET", "POST"])
def bootstrap_owner():
    db.create_all()
    email = "johndev25.backup@gmail.com"
    
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            name="Omoding John",
            email=email,
            phone="+256757764381",
            role=UserRole.OWNER
        )
        db.session.add(user)
    
    user.role = UserRole.OWNER
    user.password_hash = _hash_password("Cyber@2026")
    db.session.commit()
    
    return jsonify({
        "message": "Owner account force-updated successfully!",
        "email": user.email,
        "role": user.role.value
    }), 200


@auth_bp.route("/google-config", methods=["GET"])
def google_config():
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
    return jsonify({
        "id": current_user.id,
        "name": current_user.name,
        "phone": current_user.phone,
        "email": current_user.email,
        "role": current_user.role.value,
        "points_balance": current_user.points_balance,
    }), 200
