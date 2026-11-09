import secrets
import bcrypt
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, UserRole
from app.decorators import log_audit_event

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

def _hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


@admin_bp.route("/stats", methods=["GET"])
@login_required
def admin_stats():
    if current_user.role not in (UserRole.STAFF, UserRole.OWNER):
        return jsonify({"error": "Unauthorized access"}), 403
    
    total_users = User.query.count()
    
    # Safely count orders if the Order model exists
    total_orders = 0
    try:
        from app.models import Order
        if Order:
            total_orders = Order.query.count()
    except Exception:
        pass

    return jsonify({
        "total_users": total_users,
        "total_orders": total_orders,
    }), 200


@admin_bp.route("/update-my-password", methods=["POST"])
@login_required
def update_my_password():
    if current_user.role not in (UserRole.STAFF, UserRole.OWNER):
        return jsonify({"error": "Unauthorized access"}), 403

    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""

    if not old_password or not new_password:
        return jsonify({"error": "Both old and new passwords are required"}), 400

    if len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters long"}), 400

    if not bcrypt.checkpw(old_password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        return jsonify({"error": "Incorrect current password"}), 401

    current_user.password_hash = _hash_password(new_password)
    db.session.commit()
    
    log_audit_event(action="password_updated", entity_type="user", entity_id=current_user.id)
    return jsonify({"message": "Password updated successfully"}), 200


@admin_bp.route("/staff/reset-password", methods=["POST"])
@login_required
def admin_reset_staff_password():
    if current_user.role != UserRole.OWNER:
        return jsonify({"error": "Only the Super Admin / Owner can perform this action"}), 403

    data = request.get_json(silent=True) or {}
    target_email = (data.get("email") or "").strip().lower()

    if not target_email:
        return jsonify({"error": "Staff email address is required"}), 400

    staff_user = User.query.filter_by(email=target_email).first()
    if not staff_user:
        return jsonify({"error": "User account not found with that email"}), 404

    if staff_user.role == UserRole.OWNER and staff_user.id != current_user.id:
        return jsonify({"error": "Cannot reset another owner's password through this endpoint"}), 403

    temp_default_password = f"Temp!{secrets.token_urlsafe(6)}"
    
    staff_user.password_hash = _hash_password(temp_default_password)
    db.session.commit()

    log_audit_event(
        action="admin_reset_password", 
        entity_type="user", 
        entity_id=staff_user.id, 
        details={"reset_by": current_user.email}
    )

    return jsonify({
        "message": f"Password reset successfully for {staff_user.email}",
        "temporary_password": temp_default_password,
        "instructions": "Provide this temporary password securely to the staff member. They must log in and change it immediately."
    }), 200
    
