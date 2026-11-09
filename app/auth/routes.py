import hashlib
import hmac
import os
import time

import bcrypt
from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required, login_user, logout_user

from app.decorators import log_audit_event
from app.extensions import db, limiter
from app.models import User, UserRole

# Explicitly define Blueprint
auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

OTP_TTL_SECONDS = 300


def _otp_window(offset=0):
    return int(time.time() // OTP_TTL_SECONDS) - offset


def _otp_for(target: str, window: int) -> str:
    secret = current_app.config.get("SECRET_KEY") or "default-secret-key"
    key = secret.encode("utf-8")
    digest = hmac.new(key, f"{target}:{window}".encode("utf-8"), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def _otp_is_valid(target: str, code: str) -> bool:
    if not target or not code:
        return False
    return any(
        hmac.compare_digest(_otp_for(target, _otp_window(offset)), code)
        for offset in (0, 1)
    )


def _show_debug_otp() -> bool:
    return os.environ.get("SHOW_DEBUG_OTP", "false").strip().lower() == "true"


def _check_password(raw_password: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))


@auth_bp.route("/otp/request", methods=["POST"])
@limiter.limit("5 per minute")
def request_otp():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("phone") or data.get("email") or "").strip().lower()
    if not identifier:
        return jsonify({"error": "Phone number or email is required"}), 400

    code = _otp_for(identifier, _otp_window())
    response = {"message": "Verification code sent successfully"}
    if _show_debug_otp():
        response["debug_otp"] = code
    return jsonify(response), 200


@auth_bp.route("/otp/verify", methods=["POST"])
@limiter.limit("10 per minute")
def verify_otp_and_login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("phone") or data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()

    if not identifier or not code:
        return jsonify({"error": "Identifier and OTP code are required"}), 400

    if not _otp_is_valid(identifier, code):
        log_audit_event(action="otp_verify_failed", details={"identifier": identifier})
        return jsonify({"error": "Invalid or expired OTP code"}), 401

    is_email = "@" in identifier
    filter_kwargs = {"email": identifier} if is_email else {"phone": identifier}
    
    user = User.query.filter_by(**filter_kwargs).first()
    if user is None:
        if not name:
            return jsonify({"error": "Name is required for new customer registrations"}), 400
        
        user = User(
            name=name,
            email=identifier if is_email else None,
            phone=identifier if not is_email else None,
            role=UserRole.CLIENT
        )
        db.session.add(user)
        db.session.commit()
        log_audit_event(action="user_registered", entity_type="user", entity_id=user.id, details={"via": "otp"})

    if user.role in (UserRole.STAFF, UserRole.OWNER):
        return jsonify({"error": "Staff and owners must use secure password or approved Google login"}), 403

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "otp"})
    return jsonify({"message": "Logged in successfully", "user_id": user.id, "role": user.role.value}), 200


@auth_bp.route("/staff-login", methods=["POST"])
@limiter.limit("10 per minute")
def staff_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in (UserRole.STAFF, UserRole.OWNER) or not _check_password(password, user.password_hash):
        log_audit_event(action="staff_login_failed", details={"email": email})
        return jsonify({"error": "Invalid credentials or unauthorized access"}), 401

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "staff_password"})
    return jsonify({"message": "Logged in successfully", "user_id": user.id, "role": user.role.value}), 200


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
        return jsonify({"error": "Invalid Google credential token"}), 401

    email = (payload.get("email") or "").strip().lower()
    if not email or not payload.get("email_verified"):
        log_audit_event(action="staff_google_failed", details={"reason": "email_unverified"})
        return jsonify({"error": "Google account email is unverified"}), 401

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in (UserRole.STAFF, UserRole.OWNER):
        log_audit_event(action="staff_google_failed", details={"email": email, "reason": "not_preapproved"})
        return jsonify({"error": "This Google account has not been pre-approved for staff access"}), 403

    login_user(user)
    log_audit_event(action="login_success", entity_type="user", entity_id=user.id, details={"via": "staff_google"})
    return jsonify({"message": "Logged in successfully", "user_id": user.id, "role": user.role.value}), 200


@auth_bp.route("/google-config", methods=["GET"])
def google_config():
    return jsonify({"client_id": current_app.config.get("GOOGLE_CLIENT_ID") or ""}), 200


@auth_bp.route("/complete-profile", methods=["POST"])
@login_required
def complete_profile():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    existing = User.query.filter_by(phone=phone).first()
    if existing and existing.id != current_user.id:
        return jsonify({"error": "Phone number already in use"}), 409

    current_user.phone = phone
    db.session.commit()
    return jsonify({"message": "Profile updated successfully"}), 200


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_audit_event(action="logout", entity_type="user", entity_id=current_user.id)
    logout_user()
    return jsonify({"message": "Logged out successfully"}), 200


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
