from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.sheets_db import find_record_by, update_record_by

# Adjust blueprint name to match how it's registered in your app/__init__.py
admin_bp = Blueprint("admin", __name__)

@admin_bp.route("/shifts/<int:shift_id>/close", methods=["POST"])
@login_required
def close_shift(shift_id):
    # Retrieve the shift record from the 'Shifts' worksheet tab
    shift = find_record_by("Shifts", "id", shift_id)
    
    # Validate shift existence and ownership
    if not shift or str(shift.get("staff_user_id")) != str(current_user.id):
        return jsonify({"error": "shift not found"}), 404
        
    # Validate shift status
    if shift.get("status") != "OPEN":
        return jsonify({"error": "shift is already closed"}), 400
        
    data = request.get_json(silent=True) or {}
    try:
        closing_notes = data.get("notes", "")
        closing_cash = data.get("closing_cash", 0.0)
        
        # Update the row values directly in Google Sheets
        update_record_by("Shifts", "id", shift_id, {
            "status": "CLOSED",
            "closing_notes": closing_notes,
            "closing_cash": closing_cash
        })
        
        return jsonify({"success": True, "message": "Shift closed successfully"}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
