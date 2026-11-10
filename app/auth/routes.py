from flask import Blueprint, request, jsonify
from app.sheets_db import find_record_by

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/staff-login', methods=['POST'])
def staff_login():
    try:
        data = request.get_json() or {}
        email = str(data.get('email', '')).strip().lower()
        password = str(data.get('password', '')).strip()
        
        print(f"--> Staff Login Endpoint Hit. Email received: {email}")
        
        staff = find_record_by('Staff', 'email', email)
        print(f"--> Database lookup result: {staff}")
        
        if staff and str(staff.get('password', '')).strip() == password:
            return jsonify({"success": True, "message": "Staff login successful"})
        
        return jsonify({"success": False, "message": "Invalid email or password"}), 401
    except Exception as e:
        print(f"--> ERROR in staff_login: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@auth_bp.route('/otp/request', methods=['POST'])
def request_otp():
    data = request.get_json() or {}
    identifier = data.get('identifier')
    if not identifier:
        return jsonify({"success": False, "message": "Identifier is required"}), 400
    return jsonify({"success": True, "message": "OTP sent successfully"})
