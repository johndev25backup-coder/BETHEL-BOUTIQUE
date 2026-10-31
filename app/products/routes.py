import os
import math
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from app.decorators import login_required_json, role_required
from app.extensions import db
from app.models import (
    Category,
    DiscountRule,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductImage,
    ProductRating,
    ProductVariant,
    ProductBatchRequest,
    ProductSubmission,
    StockLedgerEntry,
    UserRole,
)

products_bp = Blueprint("products", __name__, url_prefix="/api/products")
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PRODUCT_BATCH_SIZE = 50


@products_bp.route("/upload", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def upload_product_photo():
    photos = request.files.getlist("photos") or request.files.getlist("photo")
    if not photos or not any(photo.filename for photo in photos):
        return jsonify({"error": "at least one photo is required"}), 400
    urls = []
    for photo in photos:
        extension = os.path.splitext(photo.filename)[1].lower().lstrip(".")
        if extension not in ALLOWED_IMAGE_EXTENSIONS or photo.mimetype not in ALLOWED_IMAGE_TYPES:
            return jsonify({"error": "upload JPG, PNG, WEBP, or GIF images"}), 400
        safe_name = secure_filename(photo.filename)
        if not safe_name:
            return jsonify({"error": "invalid filename"}), 400
        filename = f"{uuid4().hex}_{safe_name}"
        photo.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
        urls.append(f"/uploads/{filename}")
    return jsonify({"urls": urls, "url": urls[0]}), 201


def _normalize_product_data(data):
    if not isinstance(data, dict):
        raise ValueError("each product must be an object")
    name = (data.get("name") or "").strip()
    sku = (data.get("sku") or "").strip()
    if not name or not sku or data.get("price") is None:
        raise ValueError("name, sku and price are required")
    try:
        price = float(data["price"])
        if price <= 0 or not math.isfinite(price):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("price must be a positive number")
    try:
        stock_qty = int(data.get("stock_qty") or 0)
        if stock_qty < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("stock_qty must be a non-negative whole number")
    return {
        **data,
        "name": name,
        "sku": sku,
        "barcode": (data.get("barcode") or "").strip() or None,
        "category": (data.get("category") or "General").strip() or "General",
        "price": price,
        "stock_qty": stock_qty,
    }


def _create_product_from_data(data, stock_user_id):
    data = _normalize_product_data(data)
    name = data["name"]
    sku = data["sku"]
    price = data["price"]
    category = Category.query.filter_by(name=data["category"]).first()
    if category is None:
        category = Category(name=data["category"], variant_schema={})
        db.session.add(category)
        db.session.flush()
    product = Product(
        category_id=category.id,
        name=name,
        description=(data.get("description") or "").strip() or None,
        base_price=price,
    )
    db.session.add(product)
    db.session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        barcode=data.get("barcode"),
        size=(data.get("size") or "").strip() or None,
        colour=(data.get("colour") or "").strip() or None,
        age_range=(data.get("age_range") or "").strip() or None,
        price_override=price,
    )
    db.session.add(variant)
    db.session.flush()
    image_urls = data.get("image_urls") or ([data.get("image_url")] if data.get("image_url") else [])
    for index, image_url in enumerate(image_urls):
        image_url = (image_url or "").strip()
        if image_url:
            db.session.add(ProductImage(product_id=product.id, url=image_url, is_primary=index == 0))
    stock = data["stock_qty"]
    if stock:
        db.session.add(StockLedgerEntry(
            variant_id=variant.id, quantity_change=stock, reason="restock",
            reference_type="manual", staff_user_id=stock_user_id, notes="Initial product stock",
        ))
    return product


@products_bp.route("", methods=["POST"])
@role_required(UserRole.OWNER)
def create_product():
    data = request.get_json(silent=True) or {}
    try:
        product = _create_product_from_data(data, current_user.id)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    db.session.commit()
    return jsonify({"id": product.id, "message": "product created"}), 201


def _validated_batch(data):
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("products must be a non-empty list")
    if len(products) > MAX_PRODUCT_BATCH_SIZE:
        raise ValueError(f"a batch can contain at most {MAX_PRODUCT_BATCH_SIZE} products")
    normalized = [_normalize_product_data(product) for product in products]
    skus = [product["sku"] for product in normalized]
    if len(set(skus)) != len(skus):
        raise ValueError("each product in the batch must have a unique SKU")
    existing = ProductVariant.query.filter(ProductVariant.sku.in_(skus)).first()
    if existing:
        raise ValueError(f"SKU already exists: {existing.sku}")
    return normalized


def _batch_receipt(batch_id, operation):
    if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 64:
        raise ValueError("batch_id is required")
    receipt = ProductBatchRequest.query.filter_by(actor_id=current_user.id, batch_id=batch_id).first()
    if receipt and receipt.operation != operation:
        return None, (jsonify({"error": "batch_id was already used for another operation"}), 409)
    return receipt, None


@products_bp.route("/batch", methods=["POST"])
@role_required(UserRole.OWNER)
def create_products_batch():
    data = request.get_json(silent=True) or {}
    try:
        receipt, error = _batch_receipt(data.get("batch_id"), "publish")
        if error:
            return error
        if receipt:
            return jsonify(receipt.response_data), 201
        products_data = _validated_batch(data)
        products = [_create_product_from_data(data, current_user.id) for data in products_data]
        response_data = {"ids": [product.id for product in products], "created": len(products)}
        db.session.add(ProductBatchRequest(
            actor_id=current_user.id, batch_id=data["batch_id"], operation="publish", response_data=response_data,
        ))
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        receipt = ProductBatchRequest.query.filter_by(actor_id=current_user.id, batch_id=data.get("batch_id")).first()
        if receipt and receipt.operation == "publish":
            return jsonify(receipt.response_data), 201
        return jsonify({"error": "a product SKU already exists"}), 409
    return jsonify(response_data), 201


@products_bp.route("/submissions", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def submit_product():
    data = request.get_json(silent=True) or {}
    try:
        data = _normalize_product_data(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    submission = ProductSubmission(
        submitted_by=current_user.id,
        payload=data,
    )
    db.session.add(submission)
    db.session.commit()
    return jsonify({"id": submission.id, "message": "product submitted for super admin approval"}), 201


@products_bp.route("/submissions/batch", methods=["POST"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def submit_products_batch():
    data = request.get_json(silent=True) or {}
    try:
        receipt, error = _batch_receipt(data.get("batch_id"), "submit")
        if error:
            return error
        if receipt:
            return jsonify(receipt.response_data), 201
        products_data = _validated_batch(data)
        submissions = [ProductSubmission(submitted_by=current_user.id, payload=data) for data in products_data]
        db.session.add_all(submissions)
        response_data = {"ids": [submission.id for submission in submissions], "submitted": len(submissions)}
        db.session.add(ProductBatchRequest(
            actor_id=current_user.id, batch_id=data["batch_id"], operation="submit", response_data=response_data,
        ))
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        receipt = ProductBatchRequest.query.filter_by(actor_id=current_user.id, batch_id=data.get("batch_id")).first()
        if receipt and receipt.operation == "submit":
            return jsonify(receipt.response_data), 201
        return jsonify({"error": "batch request could not be saved"}), 409
    return jsonify(response_data), 201


def _variant_to_dict(variant: ProductVariant):
    return {
        "id": variant.id,
        "sku": variant.sku,
        "barcode": variant.barcode,
        "size": variant.size,
        "colour": variant.colour,
        "age_range": variant.age_range,
        "price": float(variant.effective_price),
        "available_stock": variant.available_stock,
    }


def _product_to_dict(product: Product):
    ratings = product.ratings
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "category": product.category.name,
        "base_price": float(product.base_price),
        "images": [img.url for img in product.images],
        "rating": {
            "average": round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else 0,
            "count": len(ratings),
        },
        "variants": [_variant_to_dict(v) for v in product.variants],
    }


@products_bp.route("/<int:product_id>/ratings", methods=["POST"])
@login_required_json
def rate_product(product_id):
    product = Product.query.get_or_404(product_id)
    if current_user.role != UserRole.CLIENT:
        return jsonify({"error": "only clients can rate products"}), 403

    purchased_variant_ids = db.session.query(ProductVariant.id).filter_by(product_id=product.id)
    has_purchased_product = (
        db.session.query(OrderItem.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.client_id == current_user.id,
            OrderItem.variant_id.in_(purchased_variant_ids),
            Order.status.in_(
                {
                    OrderStatus.PAID,
                    OrderStatus.PROCESSING,
                    OrderStatus.PACKED,
                    OrderStatus.OUT_FOR_DELIVERY,
                    OrderStatus.DELIVERED,
                    OrderStatus.COMPLETED,
                }
            ),
        )
        .first()
        is not None
    )
    if not has_purchased_product:
        return jsonify({"error": "you can only rate products you have purchased"}), 403

    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "rating must be an integer from 1 to 5"}), 400
    if rating < 1 or rating > 5:
        return jsonify({"error": "rating must be from 1 to 5"}), 400
    existing = ProductRating.query.filter_by(product_id=product.id, user_id=current_user.id).first()
    if existing:
        existing.rating = rating
        existing.comment = (data.get("comment") or "").strip() or None
    else:
        db.session.add(ProductRating(
            product_id=product.id,
            user_id=current_user.id,
            rating=rating,
            comment=(data.get("comment") or "").strip() or None,
        ))
    db.session.commit()
    return jsonify(_product_to_dict(product)["rating"]), 200


@products_bp.route("", methods=["GET"])
def list_products():
    """
    Public, read-only catalog listing. Supports the filter/search/sort
    behaviour described in the spec. This route is intentionally
    unauthenticated (anyone can browse) - only cart/checkout require login.
    """
    query = Product.query.filter_by(is_active=True)

    category_name = request.args.get("category")
    if category_name:
        query = query.join(Category).filter(Category.name == category_name)

    keyword = request.args.get("q")
    if keyword:
        like_pattern = f"%{keyword}%"
        # .ilike() is parameterized by SQLAlchemy - the keyword is bound as
        # data, never concatenated into the SQL string.
        query = query.filter(Product.name.ilike(like_pattern))

    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    if min_price is not None:
        query = query.filter(Product.base_price >= min_price)
    if max_price is not None:
        query = query.filter(Product.base_price <= max_price)

    sort = request.args.get("sort", "newest")
    if sort == "price_low_high":
        query = query.order_by(Product.base_price.asc())
    elif sort == "price_high_low":
        query = query.order_by(Product.base_price.desc())
    else:
        query = query.order_by(Product.created_at.desc())

    products = query.limit(60).all()
    return jsonify([_product_to_dict(p) for p in products]), 200


@products_bp.route("/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = Product.query.get_or_404(product_id)
    return jsonify(_product_to_dict(product)), 200


@products_bp.route("/lookup", methods=["GET"])
@role_required(UserRole.STAFF, UserRole.OWNER)
def lookup_product_by_code():
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400
    variant = ProductVariant.query.filter(
        (ProductVariant.barcode == code) | (ProductVariant.sku == code)
    ).first()
    if not variant:
        return jsonify({"error": "no product matches that barcode or SKU"}), 404
    return jsonify({"product": _product_to_dict(variant.product), "variant": _variant_to_dict(variant)}), 200


@products_bp.route("/on-sale", methods=["GET"])
def list_on_sale():
    """Surfaces items covered by an active discount rule (Section 7)."""
    from datetime import datetime
    now = datetime.utcnow()
    active_rules = DiscountRule.query.filter(
        DiscountRule.is_active.is_(True)
    ).filter(
        (DiscountRule.start_date.is_(None)) | (DiscountRule.start_date <= now)
    ).filter(
        (DiscountRule.end_date.is_(None)) | (DiscountRule.end_date >= now)
    ).all()

    category_ids = {r.category_id for r in active_rules if r.category_id}
    if category_ids:
        products = Product.query.filter(Product.category_id.in_(category_ids)).all()
    else:
        # A rule with no category = store-wide, so include everything active
        products = Product.query.filter_by(is_active=True).all() if active_rules else []

    return jsonify([_product_to_dict(p) for p in products]), 200
