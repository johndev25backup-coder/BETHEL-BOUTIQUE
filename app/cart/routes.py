from flask import Blueprint, jsonify, request, session

from app.models import ProductVariant

cart_bp = Blueprint("cart", __name__, url_prefix="/api/cart")

# Cart lives in the server-side session (not the stock table) until checkout.
# Stock is only touched - and locked - once an order is actually placed
# (see app/orders/routes.py: create_order). This keeps idle browsing from
# tying up inventory.


def _get_cart():
    return session.setdefault("cart", {})  # {variant_id_str: quantity}


@cart_bp.route("", methods=["GET"])
def view_cart():
    cart = _get_cart()
    items = []
    for variant_id_str, quantity in cart.items():
        variant = ProductVariant.query.get(int(variant_id_str))
        if not variant:
            continue
        items.append({
            "variant_id": variant.id,
            "product_name": variant.product.name,
            "size": variant.size,
            "colour": variant.colour,
            "quantity": quantity,
            "unit_price": float(variant.effective_price),
            "available_stock": variant.available_stock,
        })
    return jsonify({"items": items}), 200


@cart_bp.route("/add", methods=["POST"])
def add_to_cart():
    data = request.get_json(silent=True) or {}
    variant_id = data.get("variant_id")
    quantity = int(data.get("quantity", 1))

    variant = ProductVariant.query.get(variant_id)
    if not variant:
        return jsonify({"error": "variant not found"}), 404
    if quantity < 1:
        return jsonify({"error": "quantity must be at least 1"}), 400
    if quantity > variant.available_stock:
        return jsonify({"error": "not enough stock available", "available": variant.available_stock}), 409

    cart = _get_cart()
    cart[str(variant_id)] = cart.get(str(variant_id), 0) + quantity
    session["cart"] = cart
    session.modified = True
    return jsonify({"message": "added to cart"}), 200


@cart_bp.route("/remove", methods=["POST"])
def remove_from_cart():
    data = request.get_json(silent=True) or {}
    variant_id = str(data.get("variant_id"))

    cart = _get_cart()
    cart.pop(variant_id, None)
    session["cart"] = cart
    session.modified = True
    return jsonify({"message": "removed from cart"}), 200


@cart_bp.route("/clear", methods=["POST"])
def clear_cart():
    session["cart"] = {}
    session.modified = True
    return jsonify({"message": "cart cleared"}), 200
