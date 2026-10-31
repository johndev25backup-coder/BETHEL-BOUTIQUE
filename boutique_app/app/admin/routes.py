from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.auth.routes import _hash_password
from app.decorators import log_audit_event, owns_user_record_or_staff, role_required
from app.extensions import db
from app.models import (
    DiscountRule, PointsWithdrawal, ProductVariant, StockLedgerEntry, User,
    UserRole, WithdrawalStatus,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# ---------------------------------------------------------------------------
# Staff management (owner only)
# ---------------------------------------------------------------------------

@admin_bp.route("/staff", methods=["POST"])
@role_required(UserRole.OWNER)
def create_staff():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = data.get("name") or ""
    role_raw = data.get("role", "staff")

    if not email or not password or not name:
        return jsonify({"error": "name, email and password are required"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "email already in use"}), 409

    try:
        role = UserRole(role_raw)
    except ValueError:
        return jsonify({"error": "invalid role"}), 400
    if role == UserRole.CLIENT:
        return jsonify({"error": "use this endpoint for staff/owner accounts only"}), 400

    staff = User(name=name, email=email, password_hash=_hash_password(password), role=role)
    db.session.add(staff)
    db.session.commit()
    log_audit_event(action="staff_created", entity_type="user", entity_id=staff.id, details={"role": role.value})
    return jsonify({"message": "staff account created", "user_id": staff.id}), 201


# ---------------------------------------------------------------------------
# Stock adjustments - the highest-risk write in the whole system, so every
# call requires a reason and lands in the audit log automatically via
# StockLedgerEntry + log_audit_event.
# ---------------------------------------------------------------------------

@admin_bp.route("/stock/adjust", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def adjust_stock():
    data = request.get_json(silent=True) or {}
    variant_id = data.get("variant_id")
    quantity_change = data.get("quantity_change")
    reason = (data.get("reason") or "").strip()

    if not reason:
        return jsonify({"error": "a reason is required for every manual stock adjustment"}), 400
    variant = db.session.get(ProductVariant, variant_id)
    if not variant:
        return jsonify({"error": "variant not found"}), 404
    try:
        quantity_change = int(quantity_change)
    except (TypeError, ValueError):
        return jsonify({"error": "quantity_change must be an integer"}), 400

    entry = StockLedgerEntry(
        variant_id=variant.id, quantity_change=quantity_change, reason="adjustment",
        reference_type="manual", staff_user_id=current_user.id, notes=reason,
    )
    db.session.add(entry)
    db.session.commit()

    log_audit_event(
        action="stock_adjustment", entity_type="product_variant", entity_id=variant.id,
        details={"quantity_change": quantity_change, "reason": reason},
    )
    return jsonify({"message": "stock adjusted", "new_available_stock": variant.available_stock}), 200


@admin_bp.route("/stock/count", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def submit_stock_count():
    """Physical count reconciliation - auto-computes and logs variance."""
    data = request.get_json(silent=True) or {}
    variant_id = data.get("variant_id")
    counted_quantity = data.get("counted_quantity")

    variant = db.session.get(ProductVariant, variant_id)
    if not variant:
        return jsonify({"error": "variant not found"}), 404
    try:
        counted_quantity = int(counted_quantity)
    except (TypeError, ValueError):
        return jsonify({"error": "counted_quantity must be an integer"}), 400

    expected = variant.current_stock
    variance = counted_quantity - expected

    if variance != 0:
        db.session.add(StockLedgerEntry(
            variant_id=variant.id, quantity_change=variance, reason="count_correction",
            reference_type="stock_count", staff_user_id=current_user.id,
            notes=f"Physical count: expected {expected}, counted {counted_quantity}",
        ))
        db.session.commit()

    log_audit_event(
        action="stock_count_submitted", entity_type="product_variant", entity_id=variant.id,
        details={"expected": expected, "counted": counted_quantity, "variance": variance},
    )
    return jsonify({"expected": expected, "counted": counted_quantity, "variance": variance}), 200


# ---------------------------------------------------------------------------
# Discount rules (owner only - pricing changes are sensitive)
# ---------------------------------------------------------------------------

@admin_bp.route("/discount-rules", methods=["POST"])
@role_required(UserRole.OWNER)
def create_discount_rule():
    data = request.get_json(silent=True) or {}
    rule = DiscountRule(
        name=data.get("name", ""),
        rule_type=data.get("rule_type"),  # "aging_stock" | "seasonal"
        percent_off=data.get("percent_off"),
        threshold_days=data.get("threshold_days"),
        start_date=datetime.fromisoformat(data["start_date"]) if data.get("start_date") else None,
        end_date=datetime.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        category_id=data.get("category_id"),
        created_by=current_user.id,
    )
    db.session.add(rule)
    db.session.commit()
    log_audit_event(action="discount_rule_created", entity_type="discount_rule", entity_id=rule.id, details=data)
    return jsonify({"message": "discount rule created", "id": rule.id}), 201


# ---------------------------------------------------------------------------
# Points withdrawals - real money leaving the business, so every request
# goes through explicit owner review rather than auto-payout.
# ---------------------------------------------------------------------------

@admin_bp.route("/points-withdrawals", methods=["GET"])
@role_required(UserRole.OWNER)
def list_pending_withdrawals():
    pending = PointsWithdrawal.query.filter_by(status=WithdrawalStatus.PENDING).all()
    return jsonify([{
        "id": w.id,
        "user_id": w.user_id,
        "points_requested": w.points_requested,
        "cash_equivalent": float(w.cash_equivalent),
        "payout_method": w.payout_method.value,
        "created_at": w.created_at.isoformat(),
    } for w in pending]), 200


@admin_bp.route("/points-withdrawals/<int:withdrawal_id>/review", methods=["POST"])
@role_required(UserRole.OWNER)
def review_withdrawal(withdrawal_id):
    withdrawal = db.session.get(PointsWithdrawal, withdrawal_id)
    if not withdrawal:
        return jsonify({"error": "not found"}), 404
    if withdrawal.status != WithdrawalStatus.PENDING:
        return jsonify({"error": "already reviewed"}), 400

    data = request.get_json(silent=True) or {}
    approve = bool(data.get("approve"))

    withdrawal.status = WithdrawalStatus.APPROVED if approve else WithdrawalStatus.REJECTED
    withdrawal.reviewed_by = current_user.id
    withdrawal.reviewed_at = datetime.utcnow()

    if not approve:
        # points were already deducted when the request was submitted -
        # give them back on rejection
        client = db.session.get(User, withdrawal.user_id)
        client.points_balance += withdrawal.points_requested

    db.session.commit()
    log_audit_event(
        action="points_withdrawal_reviewed", entity_type="points_withdrawal",
        entity_id=withdrawal.id, details={"approved": approve},
    )
    # TODO: if approved, trigger an actual payout via the relevant provider
    # in app/payments/providers.py (MoMo/Airtel/Mpesa disbursement, not
    # collection), then mark status = PAID_OUT once confirmed.
    return jsonify({"message": "withdrawal reviewed", "approved": approve}), 200
