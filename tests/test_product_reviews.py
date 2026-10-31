import unittest
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Category,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductVariant,
    User,
    UserRole,
)


class ProductReviewPermissionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
        with self.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

    def test_customer_can_review_purchased_product_even_after_years(self):
        with self.app.app_context():
            owner = User(name="Alice", email="alice@example.com", password_hash="pw", role=UserRole.CLIENT)
            db.session.add(owner)
            db.session.flush()

            category = Category(name="Test Category", variant_schema={"size": True})
            db.session.add(category)
            db.session.flush()

            product = Product(category_id=category.id, name="Reviewable Dress", description="Nice", base_price=35000)
            db.session.add(product)
            db.session.flush()

            variant = ProductVariant(product_id=product.id, sku="REVIEW-001", size="M", colour="Blue")
            db.session.add(variant)
            db.session.flush()

            order = Order(
                client_id=owner.id,
                status=OrderStatus.DELIVERED,
                subtotal=35000,
                transport_charge=0,
                total=35000,
                created_at=datetime.utcnow() - timedelta(days=3650),
                updated_at=datetime.utcnow() - timedelta(days=3650),
            )
            db.session.add(order)
            db.session.flush()

            db.session.add(OrderItem(order_id=order.id, variant_id=variant.id, quantity=1, unit_price=35000))
            db.session.commit()

            self._login(owner)

            response = self.client.post(
                f"/api/products/{product.id}/ratings",
                json={"rating": 5, "comment": "Loved it after years"},
            )
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

            other_category = Category(name="Other Category", variant_schema={"size": True})
            db.session.add(other_category)
            db.session.flush()

            other_product = Product(category_id=other_category.id, name="Unpurchased Product", description="Not bought", base_price=20000)
            db.session.add(other_product)
            db.session.flush()

            db.session.add(ProductVariant(product_id=other_product.id, sku="UNPURCHASED-001", size="L", colour="Black"))
            db.session.commit()

            response = self.client.post(
                f"/api/products/{other_product.id}/ratings",
                json={"rating": 4, "comment": "Should fail"},
            )
            self.assertEqual(response.status_code, 403, response.get_data(as_text=True))

    def test_owner_product_creation_rejects_invalid_price_numbers(self):
        with self.app.app_context():
            owner = User(name="Owner", email="owner@test.com", password_hash="pw", role=UserRole.OWNER)
            db.session.add(owner)
            db.session.commit()

            self._login(owner)

            response = self.client.post(
                "/api/products",
                json={
                    "name": "Broken Product",
                    "sku": "BROKEN-INV-001",
                    "category": "General",
                    "description": "invalid price",
                    "image_url": "",
                    "size": "M",
                    "age_range": "18-30",
                    "colour": "Blue",
                    "price": float("nan"),
                    "stock_qty": 5,
                },
            )

            self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
            payload = response.get_json()
            self.assertIn("price", payload["error"]) 


if __name__ == "__main__":
    unittest.main()
