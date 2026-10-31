"""
All data access in this app goes through these SQLAlchemy models.
Because every query is built via the ORM (never raw string-concatenated SQL),
user input is always bound as a parameter, never interpreted as code.
This is what closes the SQL-injection risk across the whole app - it's a
structural choice here, not something to remember to do per-query.
"""
import enum
from datetime import datetime, timedelta

from flask_login import UserMixin

from app.extensions import db


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    CLIENT = "client"
    STAFF = "staff"
    OWNER = "owner"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAYMENT_FAILED = "payment_failed"
    PAID = "paid"
    PROCESSING = "processing"
    PACKED = "packed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ShipmentStage(str, enum.Enum):
    SOURCED_ABROAD = "sourced_abroad"
    IN_TRANSIT_INTERNATIONAL = "in_transit_international"
    ARRIVED_IN_COUNTRY = "arrived_in_country"
    DISPATCHED_TO_SHOP = "dispatched_to_shop"
    OUT_FOR_DELIVERY = "out_for_delivery"
    ARRIVED_AT_CLIENT = "arrived_at_client"
    DELIVERED = "delivered"


class ReservationStatus(str, enum.Enum):
    ACTIVE = "active"
    CONVERTED = "converted"   # payment confirmed, stock permanently deducted
    RELEASED = "released"     # expired or order cancelled, stock returned


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    MTN_MOMO = "mtn_momo"
    AIRTEL_MONEY = "airtel_money"
    MPESA = "mpesa"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class SalesChannel(str, enum.Enum):
    WALK_IN = "walk_in"
    ONLINE = "online"


class ShiftStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class PointsTxnType(str, enum.Enum):
    EARNED = "earned"
    WITHDRAWAL_REQUESTED = "withdrawal_requested"
    WITHDRAWAL_APPROVED = "withdrawal_approved"
    WITHDRAWAL_REJECTED = "withdrawal_rejected"


class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID_OUT = "paid_out"


class ReturnStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Users & Addresses
# ---------------------------------------------------------------------------

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    google_sub = db.Column(db.String(255), unique=True, nullable=True)  # Google account id
    password_hash = db.Column(db.String(255), nullable=True)  # null if Google-only, until phone added
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.CLIENT)
    points_balance = db.Column(db.Integer, nullable=False, default=0)
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    addresses = db.relationship("Address", backref="user", lazy=True)
    orders = db.relationship("Order", backref="client", lazy=True)

    # Flask-Login uses this for session identity
    def get_id(self):
        return str(self.id)

    @property
    def is_staff_or_owner(self):
        return self.role in (UserRole.STAFF, UserRole.OWNER)

    @property
    def is_owner(self):
        return self.role == UserRole.OWNER


class Address(db.Model):
    __tablename__ = "addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    label = db.Column(db.String(50))  # "Home", "Work"
    details = db.Column(db.String(255), nullable=False)
    country = db.Column(db.String(50), nullable=False, default="Uganda")
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    is_default = db.Column(db.Boolean, default=False)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    # Which variant fields apply to this category, e.g. {"size": True,
    # "colour": True, "age_range": False, "bed_size": False}
    variant_schema = db.Column(db.JSON, nullable=False, default=dict)

    products = db.relationship("Product", backref="category", lazy=True)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    base_price = db.Column(db.Numeric(12, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    images = db.relationship("ProductImage", backref="product", lazy=True)
    variants = db.relationship("ProductVariant", backref="product", lazy=True)


class ProductSubmission(db.Model):
    __tablename__ = "product_submissions"

    id = db.Column(db.Integer, primary_key=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    payload = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    review_notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)


class ProductBatchRequest(db.Model):
    __tablename__ = "product_batch_requests"
    __table_args__ = (db.UniqueConstraint("actor_id", "batch_id", name="uq_product_batch_actor_id"),)

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    batch_id = db.Column(db.String(64), nullable=False)
    operation = db.Column(db.String(24), nullable=False)
    response_data = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ProductImage(db.Model):
    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    url = db.Column(db.String(500), nullable=False)
    is_primary = db.Column(db.Boolean, default=False)

class ProductRating(db.Model):
    __tablename__ = "product_ratings"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    product = db.relationship("Product", backref=db.backref("ratings", lazy=True))
    user = db.relationship("User", backref=db.backref("product_ratings", lazy=True))


class ProductVariant(db.Model):
    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    sku = db.Column(db.String(80), unique=True, nullable=False)
    barcode = db.Column(db.String(120), unique=True, nullable=True, index=True)
    size = db.Column(db.String(30), nullable=True)
    colour = db.Column(db.String(30), nullable=True)
    age_range = db.Column(db.String(30), nullable=True)
    price_override = db.Column(db.Numeric(12, 2), nullable=True)  # falls back to product.base_price
    date_restocked = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    stock_entries = db.relationship("StockLedgerEntry", backref="variant", lazy=True)

    @property
    def current_stock(self):
        """
        Current stock is always derived by summing the append-only ledger,
        never stored/edited as a running total - see StockLedgerEntry.
        """
        total = db.session.query(
            db.func.coalesce(db.func.sum(StockLedgerEntry.quantity_change), 0)
        ).filter(StockLedgerEntry.variant_id == self.id).scalar()
        return int(total or 0)

    @property
    def reserved_stock(self):
        total = db.session.query(
            db.func.coalesce(db.func.sum(StockReservation.quantity), 0)
        ).filter(
            StockReservation.variant_id == self.id,
            StockReservation.status == ReservationStatus.ACTIVE,
        ).scalar()
        return int(total or 0)

    @property
    def available_stock(self):
        return self.current_stock - self.reserved_stock

    @property
    def effective_price(self):
        return self.price_override if self.price_override is not None else self.product.base_price


# ---------------------------------------------------------------------------
# Inventory ledger (append-only, theft/discrepancy control)
# ---------------------------------------------------------------------------

class StockLedgerEntry(db.Model):
    __tablename__ = "stock_ledger_entries"

    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=False, index=True)
    quantity_change = db.Column(db.Integer, nullable=False)  # positive = stock in, negative = stock out
    reason = db.Column(db.String(50), nullable=False)  # restock, sale, return, adjustment, write_off, count_correction
    reference_type = db.Column(db.String(50), nullable=True)  # "order", "manual", "stock_count"
    reference_id = db.Column(db.Integer, nullable=True)
    staff_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # NOTE: no update/delete methods are exposed anywhere in the app for this
    # table on purpose. Corrections are made by inserting a new offsetting
    # entry, never by editing history.


class StockReservation(db.Model):
    """
    Created the instant a client places an order, before payment completes.
    This is the 'Tray lock' - it removes units from available_stock
    immediately so two customers can't both check out the last unit.
    """
    __tablename__ = "stock_reservations"

    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.Enum(ReservationStatus), nullable=False, default=ReservationStatus.ACTIVE)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Orders, shipment tracking, delivery calls
# ---------------------------------------------------------------------------

class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING_PAYMENT)
    sales_channel = db.Column(db.Enum(SalesChannel), nullable=False, default=SalesChannel.ONLINE)
    delivery_address_id = db.Column(db.Integer, db.ForeignKey("addresses.id"), nullable=True)

    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    transport_charge = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="UGX")

    is_imported = db.Column(db.Boolean, default=False)  # drives whether shipment timeline shows

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship("OrderItem", backref="order", lazy=True)
    payments = db.relationship("Payment", backref="order", lazy=True)
    shipment_updates = db.relationship("ShipmentUpdate", backref="order", lazy=True)
    reservations = db.relationship("StockReservation", backref="order", lazy=True)


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)

    variant = db.relationship("ProductVariant")
    return_requests = db.relationship("ReturnRequest", backref="order_item", lazy=True)


class ShipmentUpdate(db.Model):
    __tablename__ = "shipment_updates"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    stage = db.Column(db.Enum(ShipmentStage), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class DeliveryCallLog(db.Model):
    """
    Logged proof that staff actually phoned the client when the item
    arrived - not just an automated notification that might go unread.
    """
    __tablename__ = "delivery_call_logs"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    outcome = db.Column(db.String(50), nullable=False)  # answered, no_answer, wrong_number, rescheduled
    notes = db.Column(db.String(255), nullable=True)
    called_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

class Shift(db.Model):
    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    staff_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.Enum(ShiftStatus), nullable=False, default=ShiftStatus.OPEN)
    opening_cash = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    closing_cash = db.Column(db.Numeric(12, 2), nullable=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    closed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=True)


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=True, index=True)
    method = db.Column(db.Enum(PaymentMethod), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="UGX")
    status = db.Column(db.Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    provider_reference = db.Column(db.String(120), nullable=True, index=True)  # external txn id
    payer_phone = db.Column(db.String(20), nullable=True)
    payment_type = db.Column(db.String(30), nullable=False, default="mobile_money")  # mobile_money | cash_on_delivery | cash_sale
    handled_by_staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # for cash sales
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at = db.Column(db.DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Discounts & promotions
# ---------------------------------------------------------------------------

class DiscountRule(db.Model):
    __tablename__ = "discount_rules"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rule_type = db.Column(db.String(20), nullable=False)  # "aging_stock" or "seasonal"
    percent_off = db.Column(db.Numeric(5, 2), nullable=False)

    # aging_stock rules
    threshold_days = db.Column(db.Integer, nullable=True)

    # seasonal rules
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)  # null = store-wide
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Loyalty points
# ---------------------------------------------------------------------------

class PointsTransaction(db.Model):
    __tablename__ = "points_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    points = db.Column(db.Integer, nullable=False)  # positive or negative
    txn_type = db.Column(db.Enum(PointsTxnType), nullable=False)
    related_order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PointsWithdrawal(db.Model):
    __tablename__ = "points_withdrawals"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    points_requested = db.Column(db.Integer, nullable=False)
    cash_equivalent = db.Column(db.Numeric(12, 2), nullable=False)
    payout_method = db.Column(db.Enum(PaymentMethod), nullable=False)
    payout_phone = db.Column(db.String(20), nullable=False)
    status = db.Column(db.Enum(WithdrawalStatus), nullable=False, default=WithdrawalStatus.PENDING)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

class ReturnRequest(db.Model):
    __tablename__ = "return_requests"

    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=False, index=True)
    reason = db.Column(db.String(255), nullable=False)
    packaging_undamaged = db.Column(db.Boolean, nullable=True)  # set by staff during inspection
    item_undamaged = db.Column(db.Boolean, nullable=True)
    status = db.Column(db.Enum(ReturnStatus), nullable=False, default=ReturnStatus.REQUESTED)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewer_notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Audit log - append-only, covers every sensitive action app-wide
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)  # e.g. "stock_adjustment", "order_void", "login_failed"
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
