from flask import Blueprint, request, jsonify
from app.sheets_db import find_record_by, insert_record

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/staff-login', methods=['POST'])
def staff_login():
    data = request.get_json() or {}
    email = data.get('email')
    password = data.get('password')
    
    # Check staff record in Google Sheets 'Staff' table
    staff = find_record_by('Staff', 'email', email)
    if staff and str(staff.get('password')) == str(password):
        return jsonify({"success": True, "message": "Staff login successful"})
    
    return jsonify({"success": False, "message": "Invalid staff credentials"}), 401

@auth_bp.route('/otp/request', methods=['POST'])
def request_otp():
    data = request.get_json() or {}
    identifier = data.get('identifier') # phone or email
    
    if not identifier:
        return jsonify({"success": False, "message": "Identifier is required"}), 400
        
    # Generate and save OTP logic here, then return JSON success
    return jsonify({"success": True, "message": "OTP sent successfully"})
