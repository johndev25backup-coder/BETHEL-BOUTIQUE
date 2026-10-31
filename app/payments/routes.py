import uuid
from datetime import datetime, timedelta

import bcrypt
from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import log_audit_event, owns_order_or_staff, role_required
from app.extensions import db, limiter
from app.models import (
    Order, OrderItem, OrderStatus, Payment, PaymentMethod, PaymentStatus,
    ProductVariant, Shift, ShiftStatus, SalesChannel,
    PointsTransaction, PointsTxnType, ReservationStatus, StockLedgerEntry,
    StockReservation, User, UserRole,
)
from app.payments.providers import PaymentProviderError, get_provider

payments_bp = Blueprint("payments", __name__, url_prefix="/api/payments")


def _transaction_id():
    return f"BETHEL-{uuid.uuid4().hex[:12].upper()}"


def _payment_receipt(payment):
    return {
        "payment_id": payment.id,
        "receipt_id": f"RCT-{payment.id:06d}",
        "transaction_id": payment.provider_reference,
        "order_id": payment.order_id,
        "method": payment.method.value,
        "payment_type": payment.payment_type,
        "payer_phone": payment.payer_phone,
        "amount": float(payment.amount),
        "currency": payment.currency,
        "status": payment.status.value,
        "created_at": payment.created_at.isoformat(),
        "confirmed_at": payment.confirmed_at.isoformat() if payment.confirmed_at else None,
    }


def _authorized_customer_payment(order, data):
    password = data.get("password") or ""
    if not current_user.is_authenticated or current_user.id != order.client_id:
        return jsonify({"error": "only the customer who placed this order can authorize payment"}), 403
    if not current_user.password_hash:
        return jsonify({"error": "set a payment password before paying", "needs_password": True}), 400
    if not bcrypt.checkpw(password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        return jsonify({"error": "incorrect payment password"}), 401
    return None


def _finalize_paid_order(order: Order, staff_user_id=None):
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
            staff_user_id=staff_user_id or order.client_id,
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
    if order.status != OrderStatus.PENDING_PAYMENT:
        return jsonify({"error": "this order is not awaiting cash payment"}), 400

    payment = Payment(
        order_id=order.id, method=PaymentMethod.CASH, amount=order.total,
        currency=order.currency, status=PaymentStatus.SUCCESS,
        provider_reference=_transaction_id(),
        payer_phone=order.client.phone if order.client else None,
        payment_type="cash_sale",
        handled_by_staff_id=current_user.id, confirmed_at=db.func.now(),
    )
    db.session.add(payment)
    _finalize_paid_order(order, current_user.id)
    return jsonify({"message": "cash payment recorded", "receipt": _payment_receipt(payment)}), 200


@payments_bp.route("/pos-sale", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def create_walk_in_sale():
    """Create, pay, and receipt a staff-recorded walk-in cash sale atomically."""
    data = request.get_json(silent=True) or {}
    shift = Shift.query.filter_by(staff_user_id=current_user.id, status=ShiftStatus.OPEN).first()
    if not shift:
        return jsonify({"error": "open a shift before recording a walk-in sale"}), 409
    line_items = data.get("items") or []
    if not isinstance(line_items, list) or not line_items:
        return jsonify({"error": "at least one item is required"}), 400

    customer_name = (data.get("customer_name") or "Walk-in Customer").strip()
    phone = (data.get("customer_phone") or "").strip() or None
    email = (data.get("customer_email") or "").strip().lower() or None
    customer = None
    if phone:
        customer = User.query.filter_by(phone=phone, role=UserRole.CLIENT).first()
    if not customer and email:
        customer = User.query.filter_by(email=email, role=UserRole.CLIENT).first()
    if not customer:
        customer = User(name=customer_name, phone=phone, email=email, role=UserRole.CLIENT)
        db.session.add(customer)
        db.session.flush()

    order = Order(client_id=customer.id, status=OrderStatus.PENDING_PAYMENT, sales_channel=SalesChannel.WALK_IN)
    db.session.add(order)
    db.session.flush()
    subtotal = 0.0
    expires_at = datetime.utcnow() + timedelta(minutes=current_app.config["STOCK_RESERVATION_MINUTES"])
    try:
        for line in line_items:
            variant_id = int(line.get("variant_id"))
            quantity = int(line.get("quantity"))
            variant = db.session.get(ProductVariant, variant_id)
            if not variant or quantity < 1:
                raise ValueError("each item needs a valid variant_id and positive quantity")
            if quantity > variant.available_stock:
                raise ValueError(f"not enough stock for {variant.sku}")
            unit_price = float(variant.effective_price)
            subtotal += unit_price * quantity
            db.session.add(OrderItem(order_id=order.id, variant_id=variant.id, quantity=quantity, unit_price=unit_price))
            db.session.add(StockReservation(
                variant_id=variant.id, order_id=order.id, quantity=quantity,
                status=ReservationStatus.ACTIVE, expires_at=expires_at,
            ))
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    order.subtotal = subtotal
    order.total = subtotal
    payment = Payment(
        order_id=order.id, method=PaymentMethod.CASH, amount=subtotal,
        currency=order.currency, status=PaymentStatus.SUCCESS,
        provider_reference=_transaction_id(), payer_phone=phone,
        payment_type="cash_sale", handled_by_staff_id=current_user.id,
        shift_id=shift.id,
        confirmed_at=db.func.now(),
    )
    db.session.add(payment)
    _finalize_paid_order(order, current_user.id)
    log_audit_event(action="walk_in_cash_sale", entity_type="order", entity_id=order.id,
                    details={"staff_user_id": current_user.id, "amount": subtotal})
    receipt = _payment_receipt(payment)
    receipt["customer_name"] = customer.name
    receipt["items"] = [{
        "variant_id": item.variant_id,
        "product_name": item.variant.product.name,
        "sku": item.variant.sku,
        "size": item.variant.size,
        "colour": item.variant.colour,
        "quantity": item.quantity,
        "unit_price": float(item.unit_price),
    } for item in order.items]
    return jsonify({"message": "walk-in cash sale completed", "order_id": order.id, "receipt": receipt}), 201


@payments_bp.route("/<int:order_id>/cash-on-delivery", methods=["POST"])
@owns_order_or_staff
def request_cash_on_delivery(order_id):
    order = db.session.get(Order, order_id)
    data = request.get_json(silent=True) or {}
    if current_user.is_staff_or_owner:
        return jsonify({"error": "use the staff cash confirmation endpoint"}), 400
    authorization_error = _authorized_customer_payment(order, data)
    if authorization_error:


        return authorization_error
    payment = Payment(order_id=order.id, method=PaymentMethod.CASH, amount=order.total,
                      currency=order.currency, status=PaymentStatus.PENDING,
                      provider_reference=_transaction_id(),
                      payer_phone=current_user.phone,
                      payment_type="cash_on_delivery")
    db.session.add(payment)
    db.session.commit()
    return jsonify({"message": "cash on delivery selected", "receipt": _payment_receipt(payment)}), 201


@payments_bp.route("/<int:order_id>/initiate", methods=["POST"])
@owns_order_or_staff
@limiter.limit("10 per minute")
def initiate_mobile_payment(order_id):
    order = db.session.get(Order, order_id)
    data = request.get_json(silent=True) or {}
    authorization_error = _authorized_customer_payment(order, data)
    if authorization_error and not current_user.is_staff_or_owner:
        return authorization_error
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
                       currency=order.currency, status=PaymentStatus.PENDING,
                       provider_reference=_transaction_id(),
                       payer_phone=phone,
                       payment_type="mobile_money")
    db.session.add(payment)
    db.session.flush()

    try:
        provider = get_provider(method.value)
        result = provider.initiate_payment(
            phone=phone, amount=float(order.total), currency=order.currency,
            order_reference=f"order-{order.id}",
        )
        payment.provider_reference = result.get("provider_reference") or payment.provider_reference

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
                "receipt": _payment_receipt(payment),
            }), 200

        db.session.commit()
        return jsonify({"message": "payment initiated", "payment_id": payment.id, "transaction_id": payment.provider_reference}), 202
    except NotImplementedError:
        db.session.rollback()
        return jsonify({
            "error": f"{method.value} integration not yet implemented - see app/payments/providers.py"
        }), 501
    except PaymentProviderError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 502


@payments_bp.route("/<int:payment_id>/cash-confirm", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def confirm_cash_on_delivery(payment_id):
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return jsonify({"error": "payment not found"}), 404
    if payment.payment_type != "cash_on_delivery" or payment.method != PaymentMethod.CASH:
        return jsonify({"error": "this is not a cash-on-delivery payment"}), 400
    if payment.status != PaymentStatus.PENDING:
        return jsonify({"error": "cash-on-delivery payment is already completed"}), 400
    payment.status = PaymentStatus.SUCCESS
    payment.handled_by_staff_id = current_user.id
    payment.confirmed_at = db.func.now()
    db.session.commit()
    _finalize_paid_order(payment.order)
    return jsonify({"message": "cash on delivery confirmed", "receipt": _payment_receipt(payment)}), 200


@payments_bp.route("/orders/<int:order_id>", methods=["GET"])
@owns_order_or_staff
def list_order_payments(order_id):
    order = db.session.get(Order, order_id)
    return jsonify([_payment_receipt(payment) for payment in order.payments]), 200


@payments_bp.route("/<int:payment_id>/reverse", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def reverse_payment(payment_id):
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return jsonify({"error": "payment not found"}), 404
    if payment.status != PaymentStatus.SUCCESS:
        return jsonify({"error": "only successful payments can be reversed"}), 400
    if payment.order.status in (OrderStatus.DELIVERED, OrderStatus.COMPLETED):
        return jsonify({"error": "delivered orders cannot be reversed from the dashboard"}), 409
    for reservation in payment.order.reservations:
        if reservation.status == ReservationStatus.CONVERTED:
            db.session.add(StockLedgerEntry(
                variant_id=reservation.variant_id,
                quantity_change=reservation.quantity,
                reason="payment_reversal",
                reference_type="payment",
                reference_id=payment.id,
                staff_user_id=current_user.id,
                notes=f"Reversal of transaction {payment.provider_reference}",
            ))
            reservation.status = ReservationStatus.RELEASED
    payment.status = PaymentStatus.FAILED
    payment.order.status = OrderStatus.PAYMENT_FAILED
    db.session.commit()
    log_audit_event(action="payment_reversed", entity_type="payment", entity_id=payment.id, details={"transaction_id": payment.provider_reference})
    return jsonify({"message": "payment reversed", "receipt": _payment_receipt(payment)}), 200


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
