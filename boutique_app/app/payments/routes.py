from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import log_audit_event, owns_order_or_staff, role_required
from app.extensions import db, limiter
from app.models import (
    Order, OrderStatus, Payment, PaymentMethod, PaymentStatus,
    PointsTransaction, PointsTxnType, ReservationStatus, StockLedgerEntry,
    StockReservation, UserRole,
)
from app.payments.providers import PaymentProviderError, get_provider

payments_bp = Blueprint("payments", __name__, url_prefix="/api/payments")


def _finalize_paid_order(order: Order):
    """
    Shared logic once a payment is confirmed, by any method: convert stock
    reservations into permanent deductions, mark the order paid, and award
    loyalty points if the order qualifies.
    """
    for reservation in order.reservations:
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        db.session.add(StockLedgerEntry(
            variant_id=reservation.variant_id,
            quantity_change=-reservation.quantity,
            reason="sale",
            reference_type="order",
            reference_id=order.id,
            staff_user_id=order.client_id,  # sale attributed to the transaction; staff-made cash sales pass staff id separately
        ))
        reservation.status = ReservationStatus.CONVERTED

    order.status = OrderStatus.PAID

    min_purchase = current_app.config["LOYALTY_MIN_PURCHASE_UGX"]
    min_points = current_app.config["LOYALTY_MIN_POINTS_AWARDED"]
    if order.currency == "UGX" and float(order.total) >= min_purchase:
        points_awarded = max(min_points, int(float(order.total) * 0.02))  # 2% of order, floored at min_points
        db.session.add(PointsTransaction(
            user_id=order.client_id, points=points_awarded,
            txn_type=PointsTxnType.EARNED, related_order_id=order.id,
        ))
        client = order.client
        client.points_balance += points_awarded

    db.session.commit()
    log_audit_event(action="order_paid", entity_type="order", entity_id=order.id)


@payments_bp.route("/<int:order_id>/cash", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def record_cash_payment(order_id):
    """Staff marks a cash sale as received - always tied to the staff member's own account."""
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"error": "order not found"}), 404

    payment = Payment(
        order_id=order.id, method=PaymentMethod.CASH, amount=order.total,
        currency=order.currency, status=PaymentStatus.SUCCESS,
        handled_by_staff_id=current_user.id, confirmed_at=db.func.now(),
    )
    db.session.add(payment)
    _finalize_paid_order(order)
    return jsonify({"message": "cash payment recorded"}), 200


@payments_bp.route("/<int:order_id>/initiate", methods=["POST"])
@owns_order_or_staff
@limiter.limit("10 per minute")
def initiate_mobile_payment(order_id):
    order = db.session.get(Order, order_id)
    data = request.get_json(silent=True) or {}
    method_raw = data.get("method")
    phone = data.get("phone")

    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        return jsonify({"error": "invalid payment method"}), 400
    if method == PaymentMethod.CASH:
        return jsonify({"error": "use /cash endpoint for cash payments"}), 400
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    payment = Payment(order_id=order.id, method=method, amount=order.total,
                       currency=order.currency, status=PaymentStatus.PENDING)
    db.session.add(payment)
    db.session.flush()

    try:
        provider = get_provider(method.value)
        result = provider.initiate_payment(
            phone=phone, amount=float(order.total), currency=order.currency,
            order_reference=f"order-{order.id}",
        )
        payment.provider_reference = result.get("provider_reference")

        if result.get("status") == "success":
            # Mock mode returns success synchronously (no real webhook
            # exists to wait for) - finalize immediately rather than
            # leaving the order stuck at "pending payment".
            payment.status = PaymentStatus.SUCCESS
            payment.confirmed_at = db.func.now()
            db.session.commit()
            _finalize_paid_order(order)
            return jsonify({
                "message": "payment successful (mock mode)" if current_app.config.get("PAYMENT_MOCK_MODE") else "payment successful",
                "payment_id": payment.id,
            }), 200

        db.session.commit()
        return jsonify({"message": "payment initiated", "payment_id": payment.id}), 202
    except NotImplementedError:
        db.session.rollback()
        return jsonify({
            "error": f"{method.value} integration not yet implemented - see app/payments/providers.py"
        }), 501
    except PaymentProviderError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 502


def _handle_provider_callback(method: PaymentMethod):
    """Shared webhook-handling logic for all three providers' callback endpoints below."""
    payload = request.get_json(silent=True) or {}
    try:
        provider = get_provider(method.value)
        result = provider.verify_callback(payload)
    except NotImplementedError:
        return jsonify({"error": "callback verification not yet implemented"}), 501

    payment = Payment.query.filter_by(provider_reference=result.get("provider_reference")).first()
    if not payment:
        return jsonify({"error": "unknown payment reference"}), 404

    if result.get("status") == "success":
        payment.status = PaymentStatus.SUCCESS
        payment.confirmed_at = db.func.now()
        db.session.commit()
        _finalize_paid_order(payment.order)
    else:
        payment.status = PaymentStatus.FAILED
        payment.order.status = OrderStatus.PAYMENT_FAILED
        db.session.commit()
        log_audit_event(action="order_payment_failed", entity_type="order", entity_id=payment.order_id)

    return jsonify({"message": "callback processed"}), 200


@payments_bp.route("/mtn/callback", methods=["POST"])
def mtn_callback():
    return _handle_provider_callback(PaymentMethod.MTN_MOMO)


@payments_bp.route("/airtel/callback", methods=["POST"])
def airtel_callback():
    return _handle_provider_callback(PaymentMethod.AIRTEL_MONEY)


@payments_bp.route("/mpesa/callback", methods=["POST"])
def mpesa_callback():
    return _handle_provider_callback(PaymentMethod.MPESA)
