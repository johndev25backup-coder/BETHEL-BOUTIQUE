"""Staff authentication backed by the Staff worksheet in Google Sheets."""
import hmac

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import limiter
from app.sheets_db import find_record_by, update_record_field

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _verify_and_upgrade_password(staff, supplied_password):
    stored = str(staff.get("password", "") or "").strip()
    if not stored:
        return False, False

    # New records use Werkzeug's salted scrypt password hash.
    if stored.startswith(("scrypt:", "pbkdf2:")):
        try:
            return check_password_hash(stored, supplied_password), False
        except (ValueError, TypeError):
            return False, False

    # One-time migration for existing plaintext rows:
    # verify without logging the password, then replace the cell with a salted hash.
    if hmac.compare_digest(stored.encode("utf-8"), supplied_password.encode("utf-8")):
        upgraded_hash = generate_password_hash(supplied_password, method="scrypt")
        update_record_field("Staff", staff["_row_number"], "password", upgraded_hash)
        return True, True

    return False, False


@auth_bp.route("/staff-login", methods=["POST"])
@limiter.limit("5 per minute")
def staff_login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A JSON request is required."}), 400

    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required."}), 400
    if len(email) > 254 or len(password) > 1024:
        return jsonify({"success": False, "message": "Invalid email or password."}), 400

    try:
        staff = find_record_by("Staff", "email", email)
        if not staff:
            return jsonify({"success": False, "message": "Invalid email or password."}), 401

        valid, upgraded = _verify_and_upgrade_password(staff, password)
        if not valid:
            return jsonify({"success": False, "message": "Invalid email or password."}), 401

        role = str(staff.get("role", "STAFF")).strip().upper()
        if role not in {"STAFF", "OWNER"}:
            return jsonify({"success": False, "message": "This account is not authorized."}), 403

        # Staff sheet currently has no ID column, so use normalized email as session identifier.
        session.clear()
        session.permanent = True
        session["staff_id"] = email
        session["staff_email"] = email
        session["staff_role"] = role
        session["authenticated"] = True

        return jsonify({
            "success": True,
            "message": "Login successful.",
            "password_upgraded": upgraded,
            "staff": {"id": email, "email": email, "role": role},
        }), 200

    except Exception:
        # Do not return sheet credentials, row contents, or exception details to the browser.
        return jsonify({
            "success": False,
            "message": "Login service is temporarily unavailable. Check server logs.",
        }), 500


@auth_bp.route("/me", methods=["GET"])
def get_current_staff():
    if not session.get("authenticated"):
        return jsonify({"authenticated": False}), 401

    return jsonify({
        "authenticated": True,
        "staff": {
            "id": session.get("staff_id"),
            "email": session.get("staff_email"),
            "role": session.get("staff_role"),
        },
    }), 200


@auth_bp.route("/logout", methods=["POST"])
def staff_logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully."}), 200
