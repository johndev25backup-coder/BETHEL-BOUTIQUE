from flask import Blueprint, request, jsonify
from app.sheets_db import find_record_by, insert_record

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/staff-login', methods=['POST'])
def staff_login():
    data = request.get_json() or {}
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    
    # Fetch all staff records and match cleanly
    staff = find_record_by('Staff', 'email', email)
    
    # Debug print for your server/runtime logs
    print(f"Attempting login for: {email} | Found in sheet: {bool(staff)}")
    
    if staff and str(staff.get('password', '')).strip() == password:
        return jsonify({"success": True, "message": "Staff login successful"})
    
    return jsonify({"success": False, "message": "Invalid email or password"}), 401

@auth_bp.route('/otp/request', methods=['POST'])
def request_otp():
    data = request.get_json() or {}
    identifier = data.get('identifier') # phone or email
    
    if not identifier:
        return jsonify({"success": False, "message": "Identifier is required"}), 400
        
    # Generate and save OTP logic here, then return JSON success
    return jsonify({"success": True, "message": "OTP sent successfully"})
