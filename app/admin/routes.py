from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy import func

from app.auth.routes import _hash_password
from app.decorators import log_audit_event, owns_user_record_or_staff, role_required
from app.extensions import db
from app.models import (
    AuditLog, DeliveryCallLog, DiscountRule, Order, OrderStatus, Payment, PaymentMethod, PaymentStatus, PointsTransaction,
    PointsWithdrawal, ProductVariant, ReturnRequest, ShipmentUpdate,
    ProductSubmission, Shift, ShiftStatus, StockLedgerEntry, User, UserRole, WithdrawalStatus,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _shift_to_dict(shift):
    cash_sales = Payment.query.filter_by(shift_id=shift.id, status=PaymentStatus.SUCCESS, method=PaymentMethod.CASH).all()
    return {
        "id": shift.id,
        "status": shift.status.value,
        "opening_cash": float(shift.opening_cash),
        "closing_cash": float(shift.closing_cash) if shift.closing_cash is not None else None,
        "cash_sales_total": sum(float(payment.amount) for payment in cash_sales),
        "opened_at": shift.opened_at.isoformat(),
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "notes": shift.notes,
    }


@admin_bp.route("/shifts/current", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def current_shift():
    shift = Shift.query.filter_by(staff_user_id=current_user.id, status=ShiftStatus.OPEN).first()
    return jsonify(_shift_to_dict(shift) if shift else None), 200


@admin_bp.route("/shifts/open", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def open_shift():
    if Shift.query.filter_by(staff_user_id=current_user.id, status=ShiftStatus.OPEN).first():
        return jsonify({"error": "you already have an open shift"}), 409
    data = request.get_json(silent=True) or {}
    try:
        opening_cash = float(data.get("opening_cash", 0))
        if opening_cash < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "opening_cash must be a non-negative number"}), 400
    shift = Shift(staff_user_id=current_user.id, opening_cash=opening_cash, notes=(data.get("notes") or "").strip() or None)
    db.session.add(shift)
    db.session.commit()
    log_audit_event(action="shift_opened", entity_type="shift", entity_id=shift.id, details={"opening_cash": opening_cash})
    return jsonify(_shift_to_dict(shift)), 201


@admin_bp.route("/shifts/<int:shift_id>/close", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def close_shift(shift_id):
    shift = db.session.get(Shift, shift_id)
    if not shift or shift.staff_user_id != current_user.id:
        return jsonify({"error": "shift not found"}), 404
    if shift.status != ShiftStatus.OPEN:
        return jsonify({"error": "shift is already closed"}), 400
    data = request.get_json(silent=True) or {}
    try:
        closing_cash = float(data.get("closing_cash"))
        if closing_cash < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "closing_cash must be a non-negative number"}), 400
    shift.closing_cash = closing_cash
    shift.status = ShiftStatus.CLOSED
    shift.closed_at = datetime.utcnow()
    db.session.commit()
    log_audit_event(action="shift_closed", entity_type="shift", entity_id=shift.id, details={"closing_cash": closing_cash})
    return jsonify(_shift_to_dict(shift)), 200


@admin_bp.route("/staff", methods=["GET"])
@role_required(UserRole.OWNER)
def list_staff():
    staff = User.query.filter(User.role.in_((UserRole.STAFF, UserRole.OWNER))).order_by(User.name).all()
    return jsonify([{
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role.value,
        "active": user.is_active_account,
    } for user in staff]), 200


@admin_bp.route("/customers", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def list_customers():
    customers = User.query.filter_by(role=UserRole.CLIENT).order_by(User.name).all()
    return jsonify([{
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
        "email": user.email,
        "total_spent": float(db.session.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.client_id == user.id).scalar() or 0),
    } for user in customers]), 200


@admin_bp.route("/customers", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def create_customer():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip() or None
    email = (data.get("email") or "").strip().lower() or None
    if not name:
        return jsonify({"error": "name is required"}), 400
    if phone and User.query.filter_by(phone=phone).first():
        return jsonify({"error": "phone already in use"}), 409
    if email and User.query.filter_by(email=email).first():
        return jsonify({"error": "email already in use"}), 409
    customer = User(name=name, phone=phone, email=email, role=UserRole.CLIENT)
    db.session.add(customer)
    db.session.commit()
    log_audit_event(action="customer_created", entity_type="user", entity_id=customer.id)
    return jsonify({"id": customer.id, "message": "customer created"}), 201


@admin_bp.route("/orders", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def list_orders():
    orders = Order.query.order_by(Order.created_at.desc()).limit(100).all()
    return jsonify([{
        "id": order.id,
        "customer": order.client.name if order.client else "Unknown",
        "customer_phone": order.client.phone if order.client else None,
        "customer_email": order.client.email if order.client else None,
        "status": order.status.value,
        "sales_channel": order.sales_channel.value,
        "total": float(order.total),
        "created_at": order.created_at.isoformat(),
        "items": [{
            "product_name": item.variant.product.name,
            "sku": item.variant.sku,
            "size": item.variant.size,
            "colour": item.variant.colour,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price),
        } for item in order.items],
        "payments": [{
            "id": payment.id,
            "method": payment.method.value,
            "status": payment.status.value,
            "transaction_id": payment.provider_reference,
            "payment_type": payment.payment_type,
            "payer_phone": payment.payer_phone,
            "amount": float(payment.amount),
        } for payment in order.payments],
    } for order in orders]), 200


@admin_bp.route("/reports/summary", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def report_summary():
    total_sales = float(db.session.query(func.coalesce(func.sum(Order.total), 0)).scalar() or 0)
    order_count = Order.query.count()
    low_stock_count = sum(1 for variant in ProductVariant.query.all() if variant.available_stock <= 2)
    return jsonify({
        "total_sales": total_sales,
        "order_count": order_count,
        "avg_order_value": total_sales / order_count if order_count else 0,
        "low_stock_count": low_stock_count,
    }), 200


@admin_bp.route("/reports/daily", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def daily_report():
    start_date_raw = request.args.get("start_date") or request.args.get("date")
    end_date_raw = request.args.get("end_date") or start_date_raw
    try:
        start_date = date.fromisoformat(start_date_raw) if start_date_raw else date.today()
        end_date = date.fromisoformat(end_date_raw) if end_date_raw else start_date
    except ValueError:
        return jsonify({"error": "start_date and end_date must use YYYY-MM-DD"}), 400
    if end_date < start_date:
        return jsonify({"error": "end_date cannot be before start_date"}), 400
    start = datetime.combine(start_date, datetime.min.time())
    end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    successful = Payment.query.filter(
        Payment.status == PaymentStatus.SUCCESS,
        Payment.created_at >= start,
        Payment.created_at < end,
    ).all()
    cash_sales = [p for p in successful if p.method == PaymentMethod.CASH]
    mobile_sales = [p for p in successful if p.method != PaymentMethod.CASH]
    order_ids = {p.order_id for p in successful}
    status_counts = {}
    report_orders = Order.query.filter(Order.id.in_(order_ids)).all() if order_ids else []
    for order in report_orders:
        status_counts[order.status.value] = status_counts.get(order.status.value, 0) + 1
    product_lines = [{
        "order_id": order.id,
        "customer": order.client.name if order.client else "Unknown",
        "product_name": item.variant.product.name,
        "sku": item.variant.sku,
        "size": item.variant.size,
        "colour": item.variant.colour,
        "quantity": item.quantity,
        "unit_price": float(item.unit_price),
        "line_total": float(item.unit_price) * item.quantity,
    } for order in report_orders for item in order.items]
    return jsonify({
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sales_total": sum(float(p.amount) for p in successful),
        "cash_total": sum(float(p.amount) for p in cash_sales),
        "mobile_total": sum(float(p.amount) for p in mobile_sales),
        "payment_count": len(successful),
        "order_count": len(order_ids),
        "orders_by_status": status_counts,
        "product_lines": product_lines,
        "low_stock_count": sum(1 for variant in ProductVariant.query.all() if variant.available_stock <= 2),
    }), 200


@admin_bp.route("/product-submissions", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def list_product_submissions():
    submissions = ProductSubmission.query.order_by(ProductSubmission.created_at.desc()).limit(100).all()
    return jsonify([{
        "id": submission.id,
        "status": submission.status,
        "payload": submission.payload,
        "submitted_by": submission.submitted_by,
        "submitted_by_name": db.session.get(User, submission.submitted_by).name,
        "created_at": submission.created_at.isoformat(),
        "review_notes": submission.review_notes,
    } for submission in submissions]), 200


@admin_bp.route("/product-submissions/<int:submission_id>/review", methods=["POST"])
@role_required(UserRole.OWNER)
def review_product_submission(submission_id):
    submission = db.session.get(ProductSubmission, submission_id)
    if not submission:
        return jsonify({"error": "product submission not found"}), 404
    if submission.status != "pending":
        return jsonify({"error": "product submission is already reviewed"}), 400
    data = request.get_json(silent=True) or {}
    approve = bool(data.get("approve"))
    if approve:
        from app.products.routes import _create_product_from_data
        try:
            _create_product_from_data(submission.payload, current_user.id)
        except ValueError as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400
    submission.status = "approved" if approve else "rejected"
    submission.reviewed_by = current_user.id
    submission.review_notes = (data.get("notes") or "").strip() or None
    submission.reviewed_at = datetime.utcnow()
    db.session.commit()
    log_audit_event(
        action="product_submission_reviewed", entity_type="product_submission", entity_id=submission.id,
        details={"approved": approve},
    )
    return jsonify({"message": "product submission reviewed", "approved": approve}), 200


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


@admin_bp.route("/staff/<int:user_id>/status", methods=["PATCH"])
@role_required(UserRole.OWNER)
def update_staff_status(user_id):
    staff = db.session.get(User, user_id)
    if not staff or staff.role not in (UserRole.STAFF, UserRole.OWNER):
        return jsonify({"error": "staff account not found"}), 404
    if staff.id == current_user.id:
        return jsonify({"error": "you cannot deactivate your own account"}), 400
    active = request.get_json(silent=True).get("active")
    if not isinstance(active, bool):
        return jsonify({"error": "active must be true or false"}), 400
    staff.is_active_account = active
    db.session.commit()
    log_audit_event(
        action="staff_account_activated" if active else "staff_account_deactivated",
        entity_type="user", entity_id=staff.id,
    )
    return jsonify({"message": "staff account status updated", "active": active}), 200


@admin_bp.route("/staff/<int:user_id>", methods=["DELETE"])
@role_required(UserRole.OWNER)
def delete_staff(user_id):
    staff = db.session.get(User, user_id)
    if not staff or staff.role not in (UserRole.STAFF, UserRole.OWNER):
        return jsonify({"error": "staff account not found"}), 404
    if staff.id == current_user.id:
        return jsonify({"error": "you cannot delete your own account"}), 400

    linked_records = [
        StockLedgerEntry.query.filter_by(staff_user_id=staff.id).first(),
        ShipmentUpdate.query.filter_by(updated_by=staff.id).first(),
        DeliveryCallLog.query.filter_by(staff_id=staff.id).first(),
        DiscountRule.query.filter_by(created_by=staff.id).first(),
        PointsWithdrawal.query.filter_by(reviewed_by=staff.id).first(),
        ReturnRequest.query.filter_by(reviewed_by=staff.id).first(),
        AuditLog.query.filter_by(user_id=staff.id).first(),
        PointsTransaction.query.filter_by(user_id=staff.id).first(),
    ]
    if any(linked_records):
        return jsonify({"error": "this account has business history; deactivate it instead"}), 409

    db.session.delete(staff)
    db.session.commit()
    log_audit_event(action="staff_account_deleted", entity_type="user", entity_id=user_id)
    return jsonify({"message": "staff account deleted"}), 200


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
