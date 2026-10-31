from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required

from app.decorators import login_required_json, log_audit_event, owns_order_or_staff, role_required
from app.extensions import db
from app.models import (
    Address, DeliveryCallLog, Order, OrderItem, OrderStatus, ProductVariant, SalesChannel,
    ReservationStatus, ReturnRequest, ReturnStatus, ShipmentStage,
    ShipmentUpdate, StockLedgerEntry, StockReservation, UserRole,
)

orders_bp = Blueprint("orders", __name__, url_prefix="/api/orders")


def _calculate_transport_charge(address: Address) -> float:
    """
    Zone-based transport pricing (simpler + cheaper to run than a live
    distance API - see the open decision flagged earlier). Swap this out
    for a Google Distance Matrix call later if/when you want true per-km
    pricing; the rest of the checkout flow doesn't need to change.
    """
    if address is None:
        return 0.0
    country = (address.country or "").strip().lower()
    if country == "kenya":
        return 25000.0  # flat cross-border fee - tune as needed
    location = (address.details or "").lower()
    if "lira" in location:
        return 0.0
    return 15000.0


@orders_bp.route("/addresses", methods=["GET"])
@login_required_json
def list_addresses():
    return jsonify([{
        "id": address.id,
        "label": address.label,
        "details": address.details,
        "country": address.country,
        "is_default": address.is_default,
    } for address in Address.query.filter_by(user_id=current_user.id).order_by(Address.id.desc()).all()]), 200


@orders_bp.route("/addresses", methods=["POST"])
@login_required_json
def create_address():
    data = request.get_json(silent=True) or {}
    details = (data.get("details") or "").strip()
    country = (data.get("country") or "Uganda").strip()
    if not details:
        return jsonify({"error": "shipping location is required"}), 400
    address = Address(
        user_id=current_user.id,
        label=(data.get("label") or "Delivery").strip(),
        details=details,
        country=country,
        is_default=not Address.query.filter_by(user_id=current_user.id).first(),
    )
    db.session.add(address)
    db.session.commit()
    return jsonify({"id": address.id, "details": address.details, "country": address.country}), 201


@orders_bp.route("/transport-preview", methods=["POST"])
@login_required_json
def transport_preview():
    data = request.get_json(silent=True) or {}
    details = (data.get("details") or "").strip()
    country = (data.get("country") or "Uganda").strip()
    if not details:
        return jsonify({"error": "shipping location is required"}), 400
    return jsonify({
        "transport_charge": _calculate_transport_charge(Address(details=details, country=country)),
        "message": "Lira delivery is free; locations outside Lira include transport charges.",
    }), 200


@orders_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    """
    Converts the session cart into an Order and locks the requested stock
    immediately, before payment happens. This is the 'Tray lock'.
    """
    cart = session.get("cart", {})
    if not cart:
        return jsonify({"error": "cart is empty"}), 400

    data = request.get_json(silent=True) or {}
    address_id = data.get("address_id")
    address = None
    if address_id:
        address = Address.query.get(address_id)
        if not address or address.user_id != current_user.id:
            # Ownership check: a client can only check out to their own
            # saved address, never someone else's by guessing an ID.
            return jsonify({"error": "invalid address"}), 400

    order = Order(client_id=current_user.id, delivery_address_id=address_id, sales_channel=SalesChannel.ONLINE)
    db.session.add(order)
    db.session.flush()  # get order.id before commit

    subtotal = 0.0
    reservation_minutes = current_app.config["STOCK_RESERVATION_MINUTES"]
    expires_at = datetime.utcnow() + timedelta(minutes=reservation_minutes)

    for variant_id_str, quantity in cart.items():
        variant = db.session.get(ProductVariant, int(variant_id_str))
        if not variant:
            db.session.rollback()
            return jsonify({"error": f"variant {variant_id_str} no longer exists"}), 409

        # Re-check availability at the moment of locking, inside the same
        # transaction, to avoid a race between two customers checking out
        # the last unit simultaneously.
        if quantity > variant.available_stock:
            db.session.rollback()
            return jsonify({
                "error": f"not enough stock for {variant.sku}",
                "available": variant.available_stock,
            }), 409

        unit_price = float(variant.effective_price)
        subtotal += unit_price * quantity

        db.session.add(OrderItem(order_id=order.id, variant_id=variant.id, quantity=quantity, unit_price=unit_price))
        db.session.add(StockReservation(
            variant_id=variant.id, order_id=order.id, quantity=quantity,
            status=ReservationStatus.ACTIVE, expires_at=expires_at,
        ))

    transport_charge = _calculate_transport_charge(address)
    order.subtotal = subtotal
    order.transport_charge = transport_charge
    order.total = subtotal + transport_charge  # discount logic can adjust this before payment in a later pass
    db.session.commit()

    session["cart"] = {}
    session.modified = True

    log_audit_event(action="order_created", entity_type="order", entity_id=order.id,
                     details={"subtotal": subtotal, "transport_charge": transport_charge})

    return jsonify({
        "order_id": order.id,
        "subtotal": subtotal,
        "transport_charge": transport_charge,
        "total": float(order.total),
        "reservation_expires_at": expires_at.isoformat(),
    }), 201


@orders_bp.route("/<int:order_id>", methods=["GET"])
@owns_order_or_staff
def get_order(order_id):
    """
    Ownership is enforced by the @owns_order_or_staff decorator on every
    call - a client cannot fetch another client's order by changing the
    ID in the URL.
    """
    order = db.session.get(Order, order_id)
    return jsonify({
        "id": order.id,
        "status": order.status.value,
        "subtotal": float(order.subtotal),
        "transport_charge": float(order.transport_charge),
        "discount_amount": float(order.discount_amount),
        "total": float(order.total),
        "is_imported": order.is_imported,
        "items": [{
            "variant_id": i.variant_id,
            "product_name": i.variant.product.name,
            "sku": i.variant.sku,
            "size": i.variant.size,
            "colour": i.variant.colour,
            "quantity": i.quantity,
            "unit_price": float(i.unit_price),
        } for i in order.items],
        "shipment_updates": [{
            "stage": s.stage.value,
            "note": s.note,
            "created_at": s.created_at.isoformat(),
        } for s in sorted(order.shipment_updates, key=lambda s: s.created_at)],
    }), 200


@orders_bp.route("/mine", methods=["GET"])
@login_required
def my_orders():
    """
    Deliberately takes no user_id parameter - it only ever returns the
    current session's own orders. This is the safest shape for a
    'my orders' endpoint: there's no ID to tamper with in the first place.
    """
    orders = Order.query.filter_by(client_id=current_user.id).order_by(Order.created_at.desc()).all()
    return jsonify([{
        "id": o.id,
        "status": o.status.value,
        "total": float(o.total),
        "created_at": o.created_at.isoformat(),
        "payments": [{
            "method": payment.method.value,
            "status": payment.status.value,
            "transaction_id": payment.provider_reference,
            "amount": float(payment.amount),
        } for payment in o.payments],
    } for o in orders]), 200


_ALLOWED_STATUS_TRANSITIONS = {
    OrderStatus.PENDING_PAYMENT: {OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.COMPLETED},
}


@orders_bp.route("/<int:order_id>/status", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def update_order_status(order_id):
    """
    Progresses an order through Processing -> Packed -> Out for Delivery ->
    Delivered -> Completed. This is separate from shipment-update below:
    this drives the order fulfillment status shown to the client, while
    shipment-update tracks the international shipping journey for
    imported items specifically. Reaching DELIVERED here is what starts
    the 2-day return window (see request_return).
    """
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"error": "order not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        new_status = OrderStatus(data.get("status"))
    except ValueError:
        return jsonify({"error": "invalid status"}), 400

    allowed_next = _ALLOWED_STATUS_TRANSITIONS.get(order.status, set())
    if new_status not in allowed_next:
        return jsonify({
            "error": f"cannot move from {order.status.value} to {new_status.value}",
            "allowed_next_statuses": [s.value for s in allowed_next],
        }), 400

    order.status = new_status
    db.session.commit()  # order.updated_at bumps automatically (onupdate=datetime.utcnow)

    log_audit_event(action="order_status_updated", entity_type="order", entity_id=order.id,
                     details={"new_status": new_status.value})
    return jsonify({"message": "order status updated", "status": order.status.value}), 200


@orders_bp.route("/<int:order_id>/shipment-update", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def add_shipment_update(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"error": "order not found"}), 404

    data = request.get_json(silent=True) or {}
    stage_raw = data.get("stage")
    try:
        stage = ShipmentStage(stage_raw)
    except ValueError:
        return jsonify({"error": "invalid stage"}), 400

    update = ShipmentUpdate(order_id=order.id, stage=stage, note=data.get("note"), updated_by=current_user.id)
    db.session.add(update)

    if stage == ShipmentStage.ARRIVED_AT_CLIENT:
        # Flags that staff must place a live phone call - not just rely on
        # the automatic SMS/WhatsApp notification.
        log_audit_event(action="delivery_call_required", entity_type="order", entity_id=order.id)

    db.session.commit()
    return jsonify({"message": "shipment updated"}), 200


@orders_bp.route("/<int:order_id>/delivery-call", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def log_delivery_call(order_id):
    """Records proof the mandatory arrival phone call actually happened."""
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"error": "order not found"}), 404

    data = request.get_json(silent=True) or {}
    outcome = data.get("outcome")
    if outcome not in ("answered", "no_answer", "wrong_number", "rescheduled"):
        return jsonify({"error": "invalid outcome"}), 400

    call_log = DeliveryCallLog(order_id=order.id, staff_id=current_user.id, outcome=outcome, notes=data.get("notes"))
    db.session.add(call_log)
    db.session.commit()
    return jsonify({"message": "call logged"}), 200


@orders_bp.route("/<int:order_id>/return-request", methods=["POST"])
@owns_order_or_staff
def request_return(order_id):
    order = db.session.get(Order, order_id)

    if order.status != OrderStatus.DELIVERED and order.status != OrderStatus.COMPLETED:
        return jsonify({"error": "order has not been delivered yet"}), 400

    return_window_days = current_app.config["RETURN_WINDOW_DAYS"]
    if datetime.utcnow() > order.updated_at + timedelta(days=return_window_days):
        return jsonify({"error": f"return window ({return_window_days} days) has passed"}), 400

    data = request.get_json(silent=True) or {}
    order_item_id = data.get("order_item_id")
    reason = data.get("reason", "")

    item = next((i for i in order.items if i.id == order_item_id), None)
    if not item:
        return jsonify({"error": "order item not found on this order"}), 404

    return_request = ReturnRequest(order_item_id=item.id, reason=reason, status=ReturnStatus.REQUESTED)
    db.session.add(return_request)
    db.session.commit()
    return jsonify({"message": "return requested", "return_request_id": return_request.id}), 201


@orders_bp.route("/return-requests/<int:return_id>/review", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def review_return(return_id):
    return_request = db.session.get(ReturnRequest, return_id)
    if not return_request:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    packaging_ok = bool(data.get("packaging_undamaged"))
    item_ok = bool(data.get("item_undamaged"))
    approve = packaging_ok and item_ok

    return_request.packaging_undamaged = packaging_ok
    return_request.item_undamaged = item_ok
    return_request.status = ReturnStatus.APPROVED if approve else ReturnStatus.REJECTED
    return_request.reviewed_by = current_user.id
    return_request.reviewer_notes = data.get("notes")
    return_request.reviewed_at = datetime.utcnow()

    if approve:
        item = return_request.order_item
        db.session.add(StockLedgerEntry(
            variant_id=item.variant_id, quantity_change=item.quantity, reason="return",
            reference_type="return_request", reference_id=return_request.id,
            staff_user_id=current_user.id,
        ))

    db.session.commit()
    log_audit_event(
        action="return_reviewed", entity_type="return_request", entity_id=return_id,
        details={"approved": approve, "packaging_ok": packaging_ok, "item_ok": item_ok},
    )
    return jsonify({"message": "return reviewed", "approved": approve}), 200
