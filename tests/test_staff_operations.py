import unittest
from datetime import datetime

from app import create_app
from app.extensions import db
from app.models import (
    Category,
    Order,
    OrderStatus,
    Product,
    ProductSubmission,
    ProductVariant,
    Shift,
    ShiftStatus,
    StockLedgerEntry,
    User,
    UserRole,
)


class StaffOperationsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            self.staff = User(name="Staff", email="staff@test.com", password_hash="pw", role=UserRole.STAFF)
            self.owner = User(name="Owner", email="owner@test.com", password_hash="pw", role=UserRole.OWNER)
            db.session.add_all([self.staff, self.owner])
            db.session.flush()
            category = Category(name="Test", variant_schema={})
            db.session.add(category)
            db.session.flush()
            product = Product(category_id=category.id, name="Dress", base_price=30000)
            db.session.add(product)
            db.session.flush()
            variant = ProductVariant(product_id=product.id, sku="DRESS-001")
            db.session.add(variant)
            db.session.flush()
            db.session.add(StockLedgerEntry(
                variant_id=variant.id, quantity_change=5, reason="restock",
                reference_type="manual", staff_user_id=self.owner.id,
            ))
            db.session.add(Shift(
                staff_user_id=self.staff.id, status=ShiftStatus.OPEN, opening_cash=0,
            ))
            db.session.commit()
            self.variant_id = variant.id
            self.staff_id = self.staff.id
            self.owner_id = self.owner.id
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_walk_in_cash_sale_generates_receipt_and_deducts_stock(self):
        self._login(self.staff_id)
        response = self.client.post("/api/payments/pos-sale", json={
            "customer_name": "Walk-in Alice",
            "customer_phone": "0700000001",
            "items": [{"variant_id": self.variant_id, "quantity": 2}],
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["receipt"]["payment_type"], "cash_sale")
        self.assertTrue(payload["receipt"]["receipt_id"].startswith("RCT-"))
        with self.app.app_context():
            variant = db.session.get(ProductVariant, self.variant_id)
            order = db.session.get(Order, payload["order_id"])
            self.assertEqual(order.status, OrderStatus.PAID)
            self.assertEqual(variant.available_stock, 3)

    def test_staff_can_close_shift_and_walk_in_sales_require_open_shift(self):
        self._login(self.staff_id)
        shift = self.client.get("/api/admin/shifts/current").get_json()
        self.assertEqual(shift["status"], "open")
        response = self.client.post(f"/api/admin/shifts/{shift['id']}/close", json={"closing_cash": 0})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        response = self.client.post("/api/payments/pos-sale", json={
            "items": [{"variant_id": self.variant_id, "quantity": 1}],
        })
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_staff_submission_requires_owner_approval_before_product_exists(self):
        self._login(self.staff_id)
        response = self.client.post("/api/products/submissions", json={
            "name": "Pending Bag", "sku": "BAG-001", "category": "Test",
            "price": 45000, "stock_qty": 4,
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        submission_id = response.get_json()["id"]
        self.assertEqual(self.client.get("/api/products").get_json()[0]["name"], "Dress")
        self._login(self.owner_id)
        response = self.client.post(f"/api/admin/product-submissions/{submission_id}/review", json={"approve": True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        names = {product["name"] for product in self.client.get("/api/products").get_json()}
        self.assertIn("Pending Bag", names)

    def test_owner_can_publish_product_batch_and_retry_without_duplicates(self):
        self._login(self.owner_id)
        batch = {
            "batch_id": "owner-batch-001",
            "products": [
                {"name": "Batch Dress", "sku": "BATCH-DRESS-001", "category": "Test", "price": 42000, "stock_qty": 3},
                {"name": "Batch Bag", "sku": "BATCH-BAG-001", "category": "Test", "price": 28000, "stock_qty": 2},
            ],
        }
        first = self.client.post("/api/products/batch", json=batch)
        retry = self.client.post("/api/products/batch", json=batch)

        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        self.assertEqual(retry.status_code, 201, retry.get_data(as_text=True))
        self.assertEqual(first.get_json(), retry.get_json())
        self.assertEqual(first.get_json()["created"], 2)
        names = {product["name"] for product in self.client.get("/api/products").get_json()}
        self.assertIn("Batch Dress", names)
        self.assertIn("Batch Bag", names)

    def test_invalid_product_batch_rolls_back_every_row(self):
        self._login(self.owner_id)
        response = self.client.post("/api/products/batch", json={
            "batch_id": "owner-batch-invalid",
            "products": [
                {"name": "Should Roll Back", "sku": "BATCH-ROLLBACK-001", "price": 42000},
                {"name": "Invalid Product", "sku": "BATCH-INVALID-001", "price": 0},
            ],
        })

        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(ProductVariant.query.filter_by(sku="BATCH-ROLLBACK-001").first())

    def test_staff_can_submit_batch_and_retry_without_duplicate_submissions(self):
        self._login(self.staff_id)
        batch = {
            "batch_id": "staff-batch-001",
            "products": [
                {"name": "Staff Batch Dress", "sku": "STAFF-BATCH-DRESS-001", "price": 42000},
                {"name": "Staff Batch Bag", "sku": "STAFF-BATCH-BAG-001", "price": 28000},
            ],
        }
        first = self.client.post("/api/products/submissions/batch", json=batch)
        retry = self.client.post("/api/products/submissions/batch", json=batch)

        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        self.assertEqual(retry.status_code, 201, retry.get_data(as_text=True))
        self.assertEqual(first.get_json(), retry.get_json())
        with self.app.app_context():
            self.assertEqual(ProductSubmission.query.filter_by(submitted_by=self.staff_id).count(), 2)

    def test_staff_can_read_daily_report_and_unpaid_order_cannot_be_processed(self):
        self._login(self.staff_id)
        report = self.client.get(f"/api/admin/reports/daily?date={datetime.utcnow().date().isoformat()}")
        self.assertEqual(report.status_code, 200, report.get_data(as_text=True))
        self.assertIn("cash_total", report.get_json())
        with self.app.app_context():
            order = Order(client_id=self.staff_id, status=OrderStatus.PENDING_PAYMENT, total=1000)
            db.session.add(order)
            db.session.commit()
            order_id = order.id
        response = self.client.post(f"/api/orders/{order_id}/status", json={"status": "processing"})
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
