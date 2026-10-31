"""
Populates the database with sample data so you have something to test
against immediately. Safe to re-run - it checks for existing records first.

Usage:
    python seed_data.py
"""
from app import create_app
from app.auth.routes import _hash_password
from app.extensions import db
from app.models import Category, Product, ProductImage, ProductVariant, User, UserRole

app = create_app()

with app.app_context():
    db.create_all()  # convenience for demo mode (SQLite); use `flask db upgrade` for real migrations

    # --- Owner account ---
    if not User.query.filter_by(email="owner@example.com").first():
        owner = User(
            name="Shop Owner", email="owner@example.com",
            password_hash=_hash_password("ChangeMe123!"), role=UserRole.OWNER,
        )
        db.session.add(owner)
        print("Created owner login: owner@example.com / ChangeMe123!")

    # --- Staff account ---
    if not User.query.filter_by(email="staff@example.com").first():
        staff = User(
            name="Shop Staff", email="staff@example.com",
            password_hash=_hash_password("ChangeMe123!"), role=UserRole.STAFF,
        )
        db.session.add(staff)
        print("Created staff login: staff@example.com / ChangeMe123!")

    db.session.commit()

    # --- Category ---
    category = Category.query.filter_by(name="Ladies' Wear").first()
    if not category:
        category = Category(
            name="Ladies' Wear",
            variant_schema={"size": True, "colour": True, "age_range": False},
        )
        db.session.add(category)
        db.session.commit()
        print("Created category: Ladies' Wear")

    # --- Product + variants ---
    if not Product.query.filter_by(name="Ankara Print Dress").first():
        product = Product(
            category_id=category.id,
            name="Ankara Print Dress",
            description="Vibrant Ankara print dress, tailored fit.",
            base_price=85000,
        )
        db.session.add(product)
        db.session.flush()

        db.session.add(ProductImage(
            product_id=product.id,
            url="https://example.com/images/ankara-dress-main.jpg",
            is_primary=True,
        ))

        variants = [
            ("ANKARA-DRESS-RED-M", "M", "Red"),
            ("ANKARA-DRESS-RED-L", "L", "Red"),
            ("ANKARA-DRESS-BLUE-M", "M", "Blue"),
        ]
        for sku, size, colour in variants:
            db.session.add(ProductVariant(
                product_id=product.id, sku=sku, size=size, colour=colour,
            ))

        db.session.commit()
        print("Created product: Ankara Print Dress (3 variants)")

        # Stock in the new variants so they're actually purchasable.
        from app.models import StockLedgerEntry
        owner = User.query.filter_by(email="owner@example.com").first()
        for variant in ProductVariant.query.filter_by(product_id=product.id).all():
            db.session.add(StockLedgerEntry(
                variant_id=variant.id, quantity_change=10, reason="restock",
                reference_type="manual", staff_user_id=owner.id,
                notes="Initial seed stock",
            ))
        db.session.commit()
        print("Stocked 10 units of each variant")

    print("\nSeeding complete. Try: GET /api/products")
