from flask import Blueprint, jsonify, request

from app.models import Category, DiscountRule, Product, ProductVariant

products_bp = Blueprint("products", __name__, url_prefix="/api/products")


def _variant_to_dict(variant: ProductVariant):
    return {
        "id": variant.id,
        "sku": variant.sku,
        "size": variant.size,
        "colour": variant.colour,
        "age_range": variant.age_range,
        "price": float(variant.effective_price),
        "available_stock": variant.available_stock,
    }


def _product_to_dict(product: Product):
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "category": product.category.name,
        "base_price": float(product.base_price),
        "images": [img.url for img in product.images],
        "variants": [_variant_to_dict(v) for v in product.variants],
    }


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
