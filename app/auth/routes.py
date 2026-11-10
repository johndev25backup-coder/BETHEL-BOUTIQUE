
import bcrypt

from flask import Blueprint, request, jsonify, session
from app.sheets_db import find_record_by

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.route("/staff-login", methods=["POST"])
def staff_login():
    try:
        data = request.get_json(silent=True) or {}

        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))

        if not email or not password:
            return jsonify({
                "success": False,
                "message": "Email and password are required"
            }), 400

        staff = find_record_by("Staff", "email", email)

        if not staff:
            return jsonify({
                "success": False,
                "message": "Invalid email or password"
            }), 401

        stored_password = str(staff.get("password", "")).strip()

        # Passwords must be stored as bcrypt hashes.
        try:
            password_valid = bcrypt.checkpw(
                password.encode("utf-8"),
                stored_password.encode("utf-8")
            )
        except (ValueError, TypeError):
            password_valid = False

        if not password_valid:
            return jsonify({
                "success": False,
                "message": "Invalid email or password"
            }), 401

        role = str(staff.get("role", "STAFF")).strip().upper()

        if role not in ("STAFF", "OWNER"):
            return jsonify({
                "success": False,
                "message": "This account is not authorized"
            }), 403

        # Start a fresh authenticated session.
        session.clear()
        session["staff_id"] = str(staff.get("id", ""))
        session["staff_email"] = email
        session["staff_role"] = role
        session["authenticated"] = True

        return jsonify({
            "success": True,
            "message": "Login successful",
            "staff": {
                "id": session["staff_id"],
                "email": email,
                "role": role
            }
        }), 200

    except Exception:
        return jsonify({
            "success": False,
            "message": "A server error occurred during login"
        }), 500


@auth_bp.route("/me", methods=["GET"])
def get_current_staff():
    if not session.get("authenticated"):
        return jsonify({
            "authenticated": False
        }), 401

    return jsonify({
        "authenticated": True,
        "staff": {
            "id": session.get("staff_id"),
            "email": session.get("staff_email"),
            "role": session.get("staff_role")
        }
    }), 200


@auth_bp.route("/logout", methods=["POST"])
def staff_logout():
    session.clear()

    return jsonify({
        "success": True,
        "message": "Logged out successfully"
    }), 200
