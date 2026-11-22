
from flask import Blueprint, request, jsonify
from app.sheets_db import find_record_by

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/staff-login", methods=["POST"])
def staff_login():
    try:
        data = request.get_json(silent=True) or {}

        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", "")).strip()

        if not email or not password:
            return jsonify({
                "success": False,
                "message": "Email and password are required"
            }), 400

        staff = find_record_by("Staff", "email", email)

        if staff and str(staff.get("password", "")).strip() == password:
            return jsonify({
                "success": True,
                "message": "Staff login successful"
            }), 200

        return jsonify({
            "success": False,
            "message": "Invalid email or password"
        }), 401

    except Exception:
        import traceback
        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": "An internal login error occurred"
        }), 500


@auth_bp.route("/otp/request", methods=["POST"])
def request_otp():
    data = request.get_json(silent=True) or {}
    identifier = data.get("identifier")

    if not identifier:
        return jsonify({
            "success": False,
            "message": "Identifier is required"
        }), 400

    return jsonify({
        "success": True,
        "message": "OTP request received"
    }), 200
